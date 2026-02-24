from django.urls import path
from . import views

urlpatterns = [
    path("",         views.index,       name="index"),
    path("status/",  views.status_view, name="status"),
    path("upload/",  views.upload_view, name="upload"),
    path("chat/",    views.chat_view,   name="chat"),
    path("reset/",   views.reset_view,  name="reset"),
]
