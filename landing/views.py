from django.shortcuts import render
from .models import TeamMember, Technology, Feature, SiteSettings

def home(request):
    """Landing page view for the development team"""
    # Get site settings (create default if none exist)
    try:
        settings = SiteSettings.objects.first()
        if not settings:
            settings = SiteSettings.objects.create()
    except:
        settings = SiteSettings.objects.create()
    
    # Get active team members
    team_members = TeamMember.objects.filter(is_active=True)
    
    # Get active technologies grouped by category
    technologies = Technology.objects.filter(is_active=True)
    tech_by_category = {}
    for tech in technologies:
        if tech.category not in tech_by_category:
            tech_by_category[tech.category] = []
        tech_by_category[tech.category].append(tech)
    
    # Get active features
    features = Feature.objects.filter(is_active=True)
    
    context = {
        'title': f'{settings.site_name}',
        'tagline': settings.tagline,
        'description': settings.description,
        'contact_email': settings.contact_email,
        'github_url': settings.github_url,
        'twitter_url': settings.twitter_url,
        'linkedin_url': settings.linkedin_url,
        'team_members': team_members,
        'technologies': tech_by_category,
        'features': features,
    }
    return render(request, 'landing/home.html', context)