import json
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from .models import ContactMessage, Feature, Project, SiteSettings, TeamMember, Technology

logger = logging.getLogger(__name__)


def home(request):
    """Render the Codeforge community landing page."""
    try:
        site_settings = SiteSettings.objects.first()
        if not site_settings:
            site_settings = SiteSettings.objects.create()
    except Exception:
        site_settings = SiteSettings()

    team_members = TeamMember.objects.filter(is_active=True)

    technologies = Technology.objects.filter(is_active=True)
    tech_by_category = {}
    for tech in technologies:
        tech_by_category.setdefault(tech.category, []).append(tech)

    features = Feature.objects.filter(is_active=True)

    projects = Project.objects.filter(is_active=True).order_by("order", "name")

    context = {
        "title": site_settings.site_name,
        "tagline": site_settings.tagline,
        "description": site_settings.description,
        "contact_email": site_settings.contact_email,
        "github_url": site_settings.github_url,
        "twitter_url": site_settings.twitter_url,
        "linkedin_url": site_settings.linkedin_url,
        "team_members": team_members,
        "technologies": tech_by_category,
        "features": features,
        "projects": projects,
        # SEO
        "canonical_url": "https://codeforge.show",
        "og_image": "https://codeforge.show/static/og-image.png",
    }
    return render(request, "landing/home.html", context)


@require_POST
def contact_submit(request):
    """Handle contact form submissions via AJAX."""
    name = request.POST.get("name", "").strip()
    email = request.POST.get("email", "").strip()
    subject = request.POST.get("subject", "").strip()
    message = request.POST.get("message", "").strip()

    # Basic validation
    errors = {}
    if not name:
        errors["name"] = "Name is required."
    if not email or "@" not in email:
        errors["email"] = "A valid email address is required."
    if not message or len(message) < 10:
        errors["message"] = "Message must be at least 10 characters."
    if len(name) > 100 or len(email) > 254 or len(subject) > 200 or len(message) > 5000:
        errors["general"] = "One or more fields exceed the maximum length."

    if errors:
        return JsonResponse({"success": False, "message": "Validation failed.", "errors": errors}, status=422)

    # Get real IP (handles reverse proxy)
    ip = (
        request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
        or request.META.get("REMOTE_ADDR")
    )

    # Save to DB
    contact = ContactMessage.objects.create(
        name=name,
        email=email,
        subject=subject or "No subject",
        message=message,
        ip_address=ip or None,
    )

    # Send notification email (best-effort — don't fail the response if email is misconfigured)
    try:
        site_settings = SiteSettings.objects.first()
        notify_email = site_settings.contact_email if site_settings else settings.DEFAULT_FROM_EMAIL
        send_mail(
            subject=f"[Codeforge] New message from {name}",
            message=(
                f"Name: {name}\n"
                f"Email: {email}\n"
                f"Subject: {subject or '—'}\n\n"
                f"Message:\n{message}\n\n"
                f"---\nMessage ID: {contact.pk}"
            ),
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@codeforge.show"),
            recipient_list=[notify_email],
            fail_silently=True,
        )
    except Exception:
        logger.exception("Failed to send contact notification email")

    return JsonResponse({"success": True, "message": "Thanks! We'll get back to you soon."})


def _base_context():
    """Shared context for standalone pages (nav/footer social links)."""
    from .models import SiteSettings
    try:
        settings_obj = SiteSettings.objects.first() or SiteSettings()
    except Exception:
        settings_obj = SiteSettings()
    return {
        "github_url": settings_obj.github_url,
        "twitter_url": settings_obj.twitter_url,
        "contact_email": settings_obj.contact_email,
    }


def projects(request):
    from .models import Project
    ctx = _base_context()
    ctx["projects"] = Project.objects.filter(is_active=True).order_by("order", "name")
    return render(request, "landing/projects.html", ctx)


def about(request):
    return render(request, "landing/about.html", _base_context())


def privacy(request):
    return render(request, "landing/privacy.html", _base_context())


def terms(request):
    return render(request, "landing/terms.html", _base_context())


def license_page(request):
    return render(request, "landing/license.html", _base_context())


def docs(request):
    return render(request, "landing/docs.html", _base_context())
