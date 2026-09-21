"""
Thin wrapper around ReadMe's API v2 (https://api.readme.com/v2).

Publishes generated onboarding readmes as Guides pages on the Clickpost
ReadMe project (https://docs.clickpost.ai). Auth is a project API key sent
as `Authorization: Bearer <key>` — see
https://docs.readme.com/main/reference/authentication.

ReadMe accepts ReadMe-flavored Markdown in `content.body`, so the generated
markdown is sent as-is.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

from app.agenticintegrations.utility.load_creds import settings

logger = logging.getLogger(__name__)


class ReadMeError(Exception):
    pass


@dataclass(frozen=True)
class PublishResult:
    page_url: str
    updated: bool


@dataclass(frozen=True)
class ExistingPage:
    slug: str
    markdown_text: str
    page_url: str


def slug_from_docs_url(url: str) -> str:
    """Pull the Guides slug from a docs URL, or treat a bare slug as-is."""
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://placeholder/{raw}")
    parts = [part for part in (parsed.path or "").split("/") if part]
    if "docs" in parts:
        index = parts.index("docs")
        if index + 1 < len(parts):
            return parts[index + 1]
    if len(parts) == 1:
        return parts[0]
    return parts[-1] if parts else raw.strip("/")


class ReadMeService:
    def __init__(self) -> None:
        if not settings.README_API_KEY:
            raise ReadMeError(
                "ReadMe is not configured - set README_API_KEY in the environment. "
                "Generate a key in the Clickpost ReadMe hub "
                "(https://readme.com/ -> Configuration -> API Keys)."
            )
        self._base_url = settings.README_API_BASE_URL.rstrip("/")
        self._branch = settings.README_BRANCH
        self._headers = {
            "Authorization": f"Bearer {settings.README_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def resolve_existing_page(self, url: str) -> ExistingPage | None:
        """Load a published Guides page from an existing_doc_links URL.

        Returns a page with empty markdown if the slug is valid but the guide
        is not on the branch yet, so the caller can still publish to that slug.
        """
        slug = slug_from_docs_url(url)
        if not slug:
            logger.warning("Could not parse a ReadMe slug from existing_doc_links=%r", url)
            return None

        page = self._get_guide(slug)
        page_url = self._page_url(page or {}, fallback_slug=slug)
        if not page:
            logger.warning(
                "Existing ReadMe page %r was not found - will publish to that slug",
                slug,
            )
            return ExistingPage(slug=slug, markdown_text="", page_url=page_url)

        content = page.get("content") or {}
        if isinstance(content, dict):
            markdown = content.get("body") or ""
        elif isinstance(content, str):
            markdown = content
        else:
            markdown = ""
        return ExistingPage(slug=page.get("slug") or slug, markdown_text=markdown, page_url=page_url)

    def update_page(self, *, title: str, markdown_text: str, slug: str) -> PublishResult:
        """Write content back to an existing Guides URL. Never rename the slug."""
        if not slug:
            raise ReadMeError("Cannot update a ReadMe page without a slug.")

        existing = self._get_guide(slug)
        category_uri = self._category_uri_from(existing) or self._resolve_category_uri()
        body = self._page_body(
            title=title,
            slug=slug,
            markdown_text=markdown_text,
            category_uri=category_uri,
            hidden=settings.README_PUBLISH_HIDDEN,
            include_slug=False,
        )
        if existing:
            logger.info("Updating existing ReadMe page %r in place", slug)
            return PublishResult(page_url=self._update_guide(slug, body), updated=True)

        logger.warning("existing_doc_links slug %r was not found - creating that exact URL", slug)
        return self._create_guide(
            self._page_body(
                title=title,
                slug=slug,
                markdown_text=markdown_text,
                category_uri=category_uri,
                hidden=settings.README_PUBLISH_HIDDEN,
            ),
            unique_on_collision=False,
        )

    def create_page(self, *, title: str, markdown_text: str, slug: str) -> PublishResult:
        """Always mint a new Guides URL. If `slug` is taken, ReadMe suffixes it."""
        if not slug:
            raise ReadMeError("Cannot publish a ReadMe page without a slug.")

        category_uri = self._resolve_category_uri()
        body = self._page_body(
            title=title,
            slug=slug,
            markdown_text=markdown_text,
            category_uri=category_uri,
            hidden=settings.README_PUBLISH_HIDDEN,
        )
        logger.info("Creating a new ReadMe page with base slug %r", slug)
        return self._create_guide(body, unique_on_collision=True)

    def _page_body(
        self,
        *,
        title: str,
        slug: str,
        markdown_text: str,
        category_uri: str,
        hidden: bool = False,
        include_slug: bool = True,
    ) -> dict:
        body: dict = {
            "title": title,
            "type": "basic",
            "category": {"uri": category_uri},
            "content": {"body": markdown_text},
        }
        if include_slug:
            body["slug"] = slug
        if hidden:
            # ReadMe v2: anyone_with_link == the Guides "Hidden" toggle.
            # Public sidebar listing uses privacy.view = "public".
            body["privacy"] = {"view": "anyone_with_link"}
        if settings.README_PARENT_SLUG:
            body["parent"] = {
                "uri": f"/branches/{self._branch}/guides/{settings.README_PARENT_SLUG}"
            }
        return body

    def _guides_url(self, slug: str | None = None) -> str:
        path = f"{self._base_url}/branches/{self._branch}/guides"
        return f"{path}/{slug}" if slug else path

    def _get_guide(self, slug: str) -> dict | None:
        response = requests.get(self._guides_url(slug), headers=self._headers, timeout=30)
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise ReadMeError(
                f"Failed to look up ReadMe page '{slug}': "
                f"{response.status_code} {response.text}"
            )
        payload = self._unwrap(response.json())
        return payload if isinstance(payload, dict) else None

    def _create_guide(self, body: dict, *, unique_on_collision: bool) -> PublishResult:
        headers = dict(self._headers)
        if not unique_on_collision:
            # Keep the requested slug. 409 instead of minting slug-1.
            headers["prefer"] = "handling=strict"
        response = requests.post(
            self._guides_url(),
            json=body,
            headers=headers,
            timeout=30,
        )
        if response.status_code == 409:
            raise ReadMeError(
                f"ReadMe slug {body.get('slug')!r} already exists and a new URL was not requested"
            )
        if response.status_code not in (200, 201):
            raise ReadMeError(
                f"Failed to create ReadMe page: {response.status_code} {response.text}"
            )
        created_url = self._page_url(self._unwrap(response.json()), fallback_slug=body["slug"])
        logger.info("Created ReadMe page at %s (requested slug=%r)", created_url, body.get("slug"))
        return PublishResult(page_url=created_url, updated=False)

    def _update_guide(self, slug: str, body: dict) -> str:
        response = requests.patch(
            self._guides_url(slug),
            json=body,
            headers={**self._headers, "prefer": "handling=strict"},
            timeout=30,
        )
        if response.status_code not in (200, 204):
            raise ReadMeError(
                f"Failed to update ReadMe page '{slug}': "
                f"{response.status_code} {response.text}"
            )
        payload = self._unwrap(response.json()) if response.content else {"slug": slug}
        returned = payload.get("slug") if isinstance(payload, dict) else None
        if returned and returned != slug:
            logger.warning(
                "ReadMe response slug %r differs from updated page %r - keeping original URL",
                returned,
                slug,
            )
        return self._page_url({"slug": slug}, fallback_slug=slug)

    def _resolve_category_uri(self) -> str:
        if settings.README_CATEGORY_URI:
            return settings.README_CATEGORY_URI

        from_slug = settings.README_CATEGORY_FROM_SLUG
        if from_slug:
            page = self._get_guide(from_slug)
            category_uri = self._category_uri_from(page)
            if category_uri:
                return category_uri
            logger.warning(
                "Could not infer ReadMe category from existing page slug %r", from_slug
            )

        category_slug = settings.README_CATEGORY_SLUG
        if category_slug:
            matched = self._find_category_uri(category_slug)
            if matched:
                return matched
            return f"/branches/{self._branch}/categories/guides/{category_slug}"

        raise ReadMeError(
            "No ReadMe category configured - set README_CATEGORY_FROM_SLUG to an "
            "existing Guides page (used to copy its category) or README_CATEGORY_SLUG."
        )

    def _find_category_uri(self, category_slug: str) -> str | None:
        response = requests.get(
            f"{self._base_url}/branches/{self._branch}/categories/guides",
            headers=self._headers,
            timeout=30,
        )
        if response.status_code != 200:
            logger.warning(
                "Failed to list ReadMe guide categories: %s %s",
                response.status_code,
                response.text,
            )
            return None

        needle = category_slug.strip().lower().replace(" ", "-")
        for category in self._unwrap(response.json()) or []:
            if not isinstance(category, dict):
                continue
            uri = category.get("uri") or ""
            uri_slug = uri.rstrip("/").split("/")[-1].lower()
            title_slug = (category.get("title") or "").strip().lower().replace(" ", "-")
            if uri_slug == needle or title_slug == needle:
                return uri
        return None

    @staticmethod
    def _category_uri_from(page: dict | None) -> str | None:
        if not page:
            return None
        category = page.get("category")
        if isinstance(category, dict):
            return category.get("uri")
        if isinstance(category, str) and category:
            return category
        return None

    @staticmethod
    def _unwrap(payload: object) -> dict | list:
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload  # type: ignore[return-value]

    def _page_url(self, data: object, *, fallback_slug: str) -> str:
        slug = fallback_slug
        if isinstance(data, dict):
            slug = data.get("slug") or fallback_slug
        base = settings.README_DOCS_BASE_URL.rstrip("/")
        return f"{base}/docs/{slug}"
