"""Small web search adapter used by LabSafe Agent.

The first version intentionally avoids browser automation and optional
dependencies so it can run on the RK3588 board. It can be replaced by a formal
search API later without changing the orchestrator contract.
"""

import html
import base64
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


class WebSearchTool:
    def __init__(self, timeout=6.0, max_results=5, provider_order=None):
        self.timeout = float(timeout)
        self.backend_timeout = max(2.0, self.timeout / 2.0)
        self.max_results = int(max_results)
        self.provider_order = provider_order or ["bing_rss", "bing", "duckduckgo"]

    def search(self, query):
        started = time.time()
        original_query = (query or "").strip()
        query = self._normalize_query(original_query)
        if not query:
            return self._result(False, query, [], "empty query", started)
        errors = []
        backends = {
            "bing": self._bing_html,
            "bing_rss": self._bing_rss,
            "duckduckgo": self._duckduckgo_html,
        }
        for name in self.provider_order:
            backend = backends.get(name)
            if not backend:
                continue
            try:
                results = backend(query)
                if results:
                    for item in results:
                        item.setdefault("source", name)
                    result = self._result(True, query, results[: self.max_results], "", started)
                    result["original_query"] = original_query
                    return result
                errors.append(f"{name}: no search results")
            except Exception as e:
                errors.append(f"{name}: {e}")
        result = self._result(False, query, [], "; ".join(errors), started)
        result["original_query"] = original_query
        return result

    def _duckduckgo_html(self, query):
        params = urllib.parse.urlencode({"q": query, "kl": "cn-zh"})
        url = f"https://duckduckgo.com/html/?{params}"
        raw = self._read_url(url)
        return self._parse_duckduckgo(raw)

    def _bing_html(self, query):
        params = urllib.parse.urlencode({"q": query, "setlang": "zh-Hans"})
        url = f"https://www.bing.com/search?{params}"
        raw = self._read_url(url)
        return self._parse_bing(raw)

    def _bing_rss(self, query):
        params = urllib.parse.urlencode({"q": query, "setlang": "zh-Hans", "format": "rss"})
        url = f"https://www.bing.com/search?{params}"
        raw = self._read_url(url)
        return self._parse_rss(raw)

    def _read_url(self, url):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 LabSafeAgent/1.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            },
        )
        with urllib.request.urlopen(req, timeout=self.backend_timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")

    def _parse_duckduckgo(self, text):
        items = []
        blocks = re.split(r'<div class="result ', text)
        for block in blocks[1:]:
            title_match = re.search(r'class="result__a" href="([^"]+)".*?>(.*?)</a>', block, re.S)
            if not title_match:
                continue
            link = self._clean_link(title_match.group(1))
            title = self._clean_html(title_match.group(2))
            snippet_match = re.search(r'class="result__snippet".*?>(.*?)</a>|class="result__snippet".*?>(.*?)</div>', block, re.S)
            snippet = ""
            if snippet_match:
                snippet = self._clean_html(snippet_match.group(1) or snippet_match.group(2) or "")
            if title and link:
                items.append({"title": title, "url": link, "snippet": snippet})
            if len(items) >= self.max_results:
                break
        return items

    def _parse_bing(self, text):
        items = []
        blocks = re.findall(
            r"<li\b[^>]*\bclass=(?:\"[^\"]*\bb_algo\b[^\"]*\"|'[^']*\bb_algo\b[^']*'|[^\s>]*\bb_algo\b[^\s>]*)[^>]*>(.*?)(?=<li\b[^>]*\bclass=|</ol>|</main>|$)",
            text,
            re.S | re.I,
        )
        if not blocks and "b_algo" in text:
            blocks = re.split(r"\bb_algo\b", text)[1:]
        for block in blocks:
            title_match = re.search(r"<h2\b.*?<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", block, re.S | re.I)
            if not title_match:
                continue
            link = self._clean_link(title_match.group(1))
            title = self._clean_html(title_match.group(2))
            snippet_match = re.search(r"<p>(.*?)</p>", block, re.S)
            snippet = self._clean_html(snippet_match.group(1)) if snippet_match else ""
            if title and link:
                items.append({"title": title, "url": link, "snippet": snippet})
            if len(items) >= self.max_results:
                break
        return items

    def _parse_rss(self, text):
        items = []
        root = ET.fromstring(text)
        for item in root.findall(".//item"):
            title = self._clean_html(item.findtext("title") or "")
            link = self._clean_link(item.findtext("link") or "")
            snippet = self._clean_html(item.findtext("description") or "")
            if title and link:
                items.append({"title": title, "url": link, "snippet": snippet})
            if len(items) >= self.max_results:
                break
        return items

    @staticmethod
    def _clean_html(value):
        value = re.sub(r"<.*?>", " ", value or "")
        value = html.unescape(value)
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _clean_link(value):
        value = html.unescape(value or "")
        parsed = urllib.parse.urlparse(value)
        params = urllib.parse.parse_qs(parsed.query)
        if "uddg" in params and params["uddg"]:
            return params["uddg"][0]
        if parsed.netloc.endswith("bing.com") and "u" in params and params["u"]:
            encoded = params["u"][0]
            if encoded.startswith("a1"):
                encoded = encoded[2:]
            try:
                padding = "=" * (-len(encoded) % 4)
                decoded = base64.urlsafe_b64decode((encoded + padding).encode("ascii")).decode("utf-8")
                if decoded.startswith(("http://", "https://")):
                    return decoded
            except Exception:
                pass
        return value

    @staticmethod
    def _normalize_query(query):
        query = (query or "").strip()
        patterns = [
            "联网搜索一下", "联网搜索", "联网查一下", "上网查一下", "网上查一下",
            "帮我搜索一下", "帮我搜索", "帮我查一下", "帮我查",
            "搜索一下", "搜一下", "查一下", "检索一下",
            "请搜索", "请查询", "搜索", "查询",
        ]
        changed = True
        while changed:
            changed = False
            for prefix in patterns:
                if query.startswith(prefix):
                    query = query[len(prefix):].strip(" ，,。:：")
                    changed = True
        return query

    @staticmethod
    def _result(success, query, results, error, started):
        return {
            "success": bool(success),
            "query": query,
            "results": results,
            "error": error,
            "latency_ms": (time.time() - started) * 1000,
        }
