from django.urls import path

from . import views

app_name = "landing"

urlpatterns = [
    path("", views.home, name="home"),
    path("contact/submit/", views.contact_submit, name="contact_submit"),
    path("about/", views.about, name="about"),
    path("privacy/", views.privacy, name="privacy"),
    path("terms/", views.terms, name="terms"),
    path("license/", views.license_page, name="license"),
    path("docs/", views.docs, name="docs"),
]
