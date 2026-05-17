from django.urls import path

from . import views

app_name = "landing"

urlpatterns = [
    path("", views.home, name="home"),
    path("contact/submit/", views.contact_submit, name="contact_submit"),
]
