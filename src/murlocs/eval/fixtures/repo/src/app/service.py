from app.render import render
from app.store import DATA


def list_widgets():
    return [render(widget) for widget in DATA["widgets"]]
