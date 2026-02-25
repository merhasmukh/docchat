from django.urls import path
from . import views

urlpatterns = [
    path("",               views.index,               name="index"),
    path("status/",        views.status_view,         name="status"),
    path("history/",       views.history_view,        name="history"),
    path("start-session/", views.start_session_view,  name="start_session"),
    path("chat/",          views.chat_view,           name="chat"),
    path("reset/",         views.reset_view,          name="reset"),
]
