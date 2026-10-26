import html
import json
import re
import urllib.parse
import urllib.request

USER_AGENT = "Tinker/1.0 (https://github.com/Aslamkv/codeforge)"
TAG_RE = re.compile(r"<[^>]+>")


class SearchFailed(Exception):
    pass


class SearchEngine:
    name = ""

    def __init__(self, timeout):
        self.timeout = timeout

    def search(self, query, count):
        return self.results(self.asked(self.address(query, count)))[:count]

    def address(self, query, count):
        raise NotImplementedError

    def results(self, answer):
        raise NotImplementedError

    def asked(self, url):
        request = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = response.read()
        try:
            return json.loads(body)
        except ValueError as error:
            raise self.failed("answered without JSON") from error

    def failed(self, why):
        return SearchFailed(f"{self.name} {why}")


class Mwmbl(SearchEngine):
    name = "mwmbl"

    def address(self, query, count):
        return "https://api.mwmbl.org/api/v1/search/?" + urllib.parse.urlencode({"s": query})

    def results(self, answer):
        if not isinstance(answer, list):
            raise self.failed("answered without a result list")
        return [
            {
                "title": joined(item.get("title")),
                "url": str(item.get("url", "")),
                "content": joined(item.get("extract")),
            }
            for item in answer
        ]


class MediaWiki(SearchEngine):
    def __init__(self, timeout, host):
        super().__init__(timeout)
        self.host = host

    def address(self, query, count):
        args = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": count,
            "format": "json",
            "maxlag": 5,
        }
        return f"https://{self.host}/w/api.php?" + urllib.parse.urlencode(args)

    def results(self, answer):
        return [
            {
                "title": str(item["title"]),
                "url": self.page(str(item["title"])),
                "content": plain(item.get("snippet", "")),
            }
            for item in self.found(answer)
        ]

    def found(self, answer):
        if isinstance(answer, dict) and "error" in answer:
            raise self.failed(f"answered with an error: {answer['error']}")
        found = answer.get("query", {}).get("search") if isinstance(answer, dict) else None
        if not isinstance(found, list):
            raise self.failed("answered without a result list")
        return found

    def page(self, title):
        return f"https://{self.host}/wiki/" + urllib.parse.quote(title.replace(" ", "_"), safe="/():,")


class Wikipedia(MediaWiki):
    name = "wikipedia"

    def __init__(self, timeout):
        super().__init__(timeout, "en.wikipedia.org")


class Wikibooks(MediaWiki):
    name = "wikibooks"

    def __init__(self, timeout):
        super().__init__(timeout, "en.wikibooks.org")


def joined(text):
    if isinstance(text, list):
        return "".join(str(segment.get("value", "")) for segment in text)
    return text if isinstance(text, str) else ""


def plain(fragment):
    return " ".join(html.unescape(TAG_RE.sub("", fragment)).split())
