from django.contrib.sitemaps import Sitemap
from django.urls import reverse


class StaticViewSitemap(Sitemap):
    """Sitemap for static pages."""

    protocol = "https"

    # (url_name, priority, changefreq)
    _pages = [
        ("landing:home",     1.0, "weekly"),
        ("landing:projects", 0.9, "weekly"),
        ("landing:about",    0.8, "monthly"),
        ("landing:docs",     0.6, "monthly"),
        ("landing:privacy",  0.3, "yearly"),
        ("landing:terms",    0.3, "yearly"),
        ("landing:license",  0.3, "yearly"),
    ]

    def items(self):
        return self._pages

    def location(self, item):
        return reverse(item[0])

    def priority(self, item):
        return item[1]

    def changefreq(self, item):
        return item[2]
