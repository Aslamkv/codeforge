import pytest

from tinkerkit.docsite import DocCache, DocSite, candidates, map_links, pick, under, version_keys
from tinkerkit.fetcher import Fetched
from tinkerkit.passages import Block, Page
from tinkerkit.settings import Config, Home


class SiteFetcher:
    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def fetch_all(self, urls, kind="fetch"):
        self.asked += urls
        return [Fetched(url, 200, url, self.pages[url], "text/html") if url in self.pages else
                Fetched(url, 404, url, error="HTTP 404") for url in urls]


def text(words):
    return f"<main><p>{(words + ' ') * 30}</p></main>"


@pytest.mark.spec("LRN-14")
def test_the_versions_documentation_is_tried_most_exact_first():
    assert version_keys("3.15.1") == ["3.15.1", "3.15", "3", "latest"] and version_keys("") == ["latest"]
    docs = {"root": "https://docs.x.org/3/", "versioned": "https://docs.x.org/{version}/"}
    assert candidates(docs, "3.15.1") == [("3.15.1", "https://docs.x.org/3.15.1/"), ("3.15", "https://docs.x.org/3.15/"),
                                          ("3", "https://docs.x.org/3/"), ("latest", "https://docs.x.org/3/")]
    assert candidates({"root": "https://x.dev/", "versioned": ""}, "2.0") == [("latest", "https://x.dev/")]
    fetcher = SiteFetcher({"https://docs.x.org/3.15/": text("root")})
    root, key = DocSite(fetcher).resolve(docs, "3.15.1")
    assert (root.url, key) == ("https://docs.x.org/3.15/", "3.15") and fetcher.asked[0] == "https://docs.x.org/3.15.1/"
    assert DocSite(SiteFetcher({})).resolve(docs, "3.15.1") == (None, "")


@pytest.mark.spec("LRN-15")
def test_the_sites_map_comes_from_llms_txt_then_sitemap_then_its_own_links():
    base = "https://docs.x.org/3.15/"
    llms = "# X\n- [What's new](https://docs.x.org/3.15/whatsnew/3.15.html)\n- [Blog](https://blog.x.org/a)"
    sitemap = "<urlset><url><loc>https://docs.x.org/3.15/tutorial/</loc></url><url><loc> https://docs.x.org/3.14/old </loc></url></urlset>"
    assert map_links("llms.txt", llms) == [("https://docs.x.org/3.15/whatsnew/3.15.html", "What's new"), ("https://blog.x.org/a", "Blog")]
    assert map_links("sitemap.xml", sitemap) == [("https://docs.x.org/3.15/tutorial/", ""), ("https://docs.x.org/3.14/old", "")]
    assert under(map_links("sitemap.xml", sitemap), base) == [("https://docs.x.org/3.15/tutorial/", "")]
    root = Fetched(base, 200, base, '<a href="faq.html">FAQ</a> <a href="https://elsewhere.org/">x</a>', "text/html")
    with_llms = SiteFetcher({base + "llms.txt": llms})
    assert [url for url, _ in DocSite(with_llms).map(root, base)] == ["https://docs.x.org/3.15/whatsnew/3.15.html"]
    with_sitemap = SiteFetcher({base + "sitemap.xml": sitemap})
    assert [url for url, _ in DocSite(with_sitemap).map(root, base)] == ["https://docs.x.org/3.15/tutorial/"]
    its_own_links_on_its_site = [(base + "faq.html", "FAQ")]
    assert DocSite(SiteFetcher({})).map(root, base) == its_own_links_on_its_site


@pytest.mark.spec("LRN-15")
def test_each_slot_gets_its_pages_in_turn_the_versions_first():
    base = "https://docs.x.org/3.15/"
    another_versions_page = base + "whatsnew/3.14.html"
    links = [(another_versions_page, "What's new in 3.14"), (base + "whatsnew/3.15.html", "What's new in 3.15"),
             (base + "tutorial/index.html", "Tutorial"), (base + "tutorial/classes.html", "Classes"),
             (base + "faq/design.html", "Design FAQ"), (base + "license.html", "License")]
    wanted = ["whatsnew changelog", "tutorial reference", "faq design"]
    assert pick(links, wanted, "3.15", 10, base) == [base + "whatsnew/3.15.html", base + "tutorial/index.html",
                                                     base + "faq/design.html", base + "tutorial/classes.html"]
    assert len(pick(links, wanted, "3.15", 2, base)) == 2


@pytest.mark.spec("LRN-16")
def test_reading_a_site_follows_an_index_one_hop_to_its_versions_page():
    base = "https://docs.x.org/3.15/"
    pages = {base: '<main><a href="whatsnew/index.html">What\'s new</a>' + text("Overview of X")[6:],
             base + "whatsnew/index.html": '<ul><li><a href="3.15.html">3.15</a></li><li><a href="3.14.html">3.14</a></li></ul>',
             base + "whatsnew/3.15.html": text("New in 3.15: faster startup"),
             base + "whatsnew/3.14.html": text("New in 3.14")}
    fetcher = SiteFetcher(pages)
    read = DocSite(fetcher).read(fetcher.fetch_all([base])[0], "3.15", ["whatsnew"], 10)
    the_index_and_its_versions_page = [base, base + "whatsnew/3.15.html"]
    assert [page.url for page in read] == the_index_and_its_versions_page
    assert base + "whatsnew/3.14.html" not in fetcher.asked


@pytest.mark.spec("LRN-17")
def test_the_documentation_is_kept_per_topic_and_version(tinker_home):
    base = "https://docs.x.org/3.15/"
    fetcher = SiteFetcher({base: text("Overview of X")})
    docs = {"root": "https://docs.x.org/3/", "versioned": "https://docs.x.org/{version}/"}
    pages, key, kept = DocSite(fetcher).pages("x", docs, "3.15.2", ["overview"], 10)
    assert key == "3.15" and not kept and pages[0].blocks
    again = SiteFetcher({})
    pages_again, key_again, kept_again = DocSite(again).pages("x", docs, "3.15.2", ["overview"], 10)
    assert (key_again, kept_again, again.asked) == ("3.15", True, []) and pages_again[0].blocks == pages[0].blocks
    assert Home.store().get("sources", "x@3.15")["root"] == base


@pytest.mark.spec("LRN-17")
def test_a_copy_of_the_latest_documentation_is_read_again_after_docs_latest_days(tinker_home):
    page = Page("https://x.dev/", "t", [Block("text", "Overview")])
    DocCache.put("x", "latest", "https://x.dev/", [page])
    assert DocCache.get("x", "latest")[0].blocks == page.blocks
    Home.store().update("sources", "x@latest", lambda kept: dict(kept, kept="2020-01-01 10:00"))
    assert DocCache.get("x", "latest") is None
    DocCache.put("x", "1.0", "https://x.dev/1.0/", [page])
    Home.store().update("sources", "x@1.0", lambda kept: dict(kept, kept="2020-01-01 10:00"))
    a_versions_own_documentation = DocCache.get("x", "1.0")
    assert a_versions_own_documentation is not None
    Config.save(docs_latest_days=100000)
    assert DocCache.get("x", "latest") is not None


@pytest.mark.spec("LRN-16")
def test_the_latest_documentation_is_matched_to_the_version_installed():
    base = "https://docs.x.org/3/"
    pages = {base: '<main><a href="whatsnew/3.15.html">3.15</a> <a href="whatsnew/3.14.html">3.14</a>' + text("X")[6:],
             base + "whatsnew/3.15.html": text("New in 3.15"), base + "whatsnew/3.14.html": text("New in 3.14")}
    fetcher = SiteFetcher(pages)
    read = DocSite(fetcher).read(fetcher.fetch_all([base])[0], "latest", ["whatsnew"], 10, version="3.15.0")
    assert [page.url for page in read] == [base, base + "whatsnew/3.15.html"]
