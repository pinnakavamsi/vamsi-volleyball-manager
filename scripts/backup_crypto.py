"""Streaming authenticated encryption for operator recovery objects.

Format: VMBACKUP1 + random 12-byte nonce + AES-256-GCM ciphertext + 16-byte tag.
The format/nonce are authenticated; no unauthenticated plaintext is published.
"""
import base64
import os
from pathlib import Path
from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes

MAGIC=b'VMBACKUP1'
CHUNK=1024*1024


def read_key(path):
    path=Path(path)
    if path.stat().st_mode & 0o077: raise RuntimeError('Recovery key must be readable only by its owner (chmod 600).')
    value=base64.urlsafe_b64decode(path.read_bytes().strip())
    if len(value)!=32: raise RuntimeError('Recovery key must encode 32 random bytes.')
    return value


def encrypt_file(source,destination,key):
    source,destination=Path(source),Path(destination);destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    nonce=os.urandom(12);header=MAGIC+nonce
    cipher=Cipher(algorithms.AES(key),modes.GCM(nonce)).encryptor();cipher.authenticate_additional_data(header)
    temporary=destination.with_name(destination.name+'.'+os.urandom(8).hex()+'.partial')
    try:
        with source.open('rb') as incoming,temporary.open('xb') as outgoing:
            os.chmod(temporary,0o600);outgoing.write(header)
            while block:=incoming.read(CHUNK): outgoing.write(cipher.update(block))
            outgoing.write(cipher.finalize());outgoing.write(cipher.tag);outgoing.flush();os.fsync(outgoing.fileno())
        temporary.replace(destination)
        descriptor=os.open(destination.parent,os.O_RDONLY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
    finally: temporary.unlink(missing_ok=True)


def decrypt_file(source,destination,key):
    source,destination=Path(source),Path(destination)
    if source.stat().st_size<len(MAGIC)+12+16: raise RuntimeError('Invalid encrypted recovery object.')
    temporary=destination.with_name(destination.name+'.'+os.urandom(8).hex()+'.partial')
    destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    try:
        with source.open('rb') as incoming:
            header=incoming.read(len(MAGIC)+12)
            if not header.startswith(MAGIC): raise RuntimeError('Unknown recovery encryption format.')
            incoming.seek(-16,2);tag=incoming.read(16);incoming.seek(len(header))
            remaining=source.stat().st_size-len(header)-16
            cipher=Cipher(algorithms.AES(key),modes.GCM(header[len(MAGIC):],tag)).decryptor();cipher.authenticate_additional_data(header)
            with temporary.open('xb') as outgoing:
                os.chmod(temporary,0o600)
                while remaining:
                    block=incoming.read(min(CHUNK,remaining))
                    if not block: raise RuntimeError('Truncated recovery object.')
                    remaining-=len(block);outgoing.write(cipher.update(block))
                outgoing.write(cipher.finalize());outgoing.flush();os.fsync(outgoing.fileno())
        temporary.replace(destination)
    finally: temporary.unlink(missing_ok=True)
