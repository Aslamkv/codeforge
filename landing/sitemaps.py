from django.contrib.sitemaps import Sitemap
from django.urls import reverse


class StaticViewSitemap(Sitemap):
    """Sitemap for static pages (home, contact anchor)."""

    priority = 1.0
    changefreq = "weekly"
    protocol = "https"

    def items(self):
        return ["landing:home"]

    def location(self, item):
        return reverse(item)
