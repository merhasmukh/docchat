from django.urls import path
from . import views

urlpatterns = [
    path("",              views.index,             name="index"),
    path("widget/",       views.widget_view,       name="widget"),
    path("status/",       views.status_view,       name="status"),
    path("history/",      views.history_view,      name="history"),
    path("session-config/", views.session_config_view, name="session_config"),
    path("start-session/",  views.start_session_view,  name="start_session"),
    path("request-otp/",  views.request_otp_view,  name="request_otp"),
    path("verify-otp/",   views.verify_otp_view,   name="verify_otp"),
    path("resend-otp/",   views.resend_otp_view,   name="resend_otp"),
    path("chat/",         views.chat_view,         name="chat"),
    path("reset/",        views.reset_view,        name="reset"),
]
