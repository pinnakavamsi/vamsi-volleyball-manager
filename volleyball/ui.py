from flask import Blueprint, render_template

bp = Blueprint("ui", __name__)


@bp.get("/")
def organizer():
    return render_template("index.html", experience="organizer", slug="")


@bp.get("/login")
@bp.get("/setup")
@bp.get("/reset")
@bp.get("/invitation")
@bp.get("/invite")
def identity():
    return render_template("index.html", experience="identity", slug="")


@bp.get("/referee")
def referee():
    return render_template("index.html", experience="referee", slug="")


@bp.get("/t/<slug>")
def public(slug):
    return render_template("index.html", experience="public", slug=slug)


@bp.get("/display/<slug>")
def display(slug):
    return render_template("index.html", experience="display", slug=slug)
