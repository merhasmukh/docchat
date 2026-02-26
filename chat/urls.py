from django.urls import path
from . import views

urlpatterns = [
    path("",              views.index,             name="index"),
    path("status/",       views.status_view,       name="status"),
    path("history/",      views.history_view,      name="history"),
    path("request-otp/",  views.request_otp_view,  name="request_otp"),
    path("verify-otp/",   views.verify_otp_view,   name="verify_otp"),
    path("resend-otp/",   views.resend_otp_view,   name="resend_otp"),
    path("chat/",         views.chat_view,         name="chat"),
    path("reset/",        views.reset_view,        name="reset"),
]
