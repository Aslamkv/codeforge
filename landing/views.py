from django.shortcuts import render
from .models import TeamMember, Technology, Feature, SiteSettings


def home(request):
    """Render the Codeforge community landing page."""
    try:
        settings = SiteSettings.objects.first()
        if not settings:
            settings = SiteSettings.objects.create()
    except Exception:
        settings = SiteSettings()

    team_members = TeamMember.objects.filter(is_active=True)

    technologies = Technology.objects.filter(is_active=True)
    tech_by_category = {}
    for tech in technologies:
        tech_by_category.setdefault(tech.category, []).append(tech)

    features = Feature.objects.filter(is_active=True)

    context = {
        "title": settings.site_name,
        "tagline": settings.tagline,
        "description": settings.description,
        "contact_email": settings.contact_email,
        "github_url": settings.github_url,
        "twitter_url": settings.twitter_url,
        "linkedin_url": settings.linkedin_url,
        "team_members": team_members,
        "technologies": tech_by_category,
        "features": features,
    }
    return render(request, "landing/home.html", context)
