"""Shared HTTP plumbing for the public data sources."""

import xml.etree.ElementTree as ET
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx

from app.core.config import settings

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


@asynccontextmanager
async def http_client(
    # Not a cancel-scope deadline: it is handed straight to httpx, which is what should
    # own the timeout for an outbound call to a public service.
    timeout: httpx.Timeout | float = DEFAULT_TIMEOUT,  # noqa: ASYNC109
) -> AsyncGenerator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        timeout=timeout,
        headers={"User-Agent": settings.http_user_agent},
        follow_redirects=True,
    ) as client:
        yield client


def strip_ns(tag: str) -> str:
    """Element tag without its XML namespace. Catastro and IGN namespace everything."""
    return tag.split("}")[-1]


def find_text(root: ET.Element, name: str) -> str:
    for element in root.iter():
        if strip_ns(element.tag) == name and element.text:
            return element.text.strip()
    return ""


def find_all_text(root: ET.Element, name: str) -> list[str]:
    return [
        element.text.strip()
        for element in root.iter()
        if strip_ns(element.tag) == name and element.text
    ]
