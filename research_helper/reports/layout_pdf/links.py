from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum

import fitz

LinkValue = str | int | float | fitz.Rect | fitz.Point
LinkFingerprint = tuple[str | int, ...]


class LinkType(str, Enum):
    URI = "uri"
    GOTO = "goto"
    GOTOR = "gotor"
    LAUNCH = "launch"


@dataclass(frozen=True, slots=True)
class LinkSpec:
    link_type: LinkType
    rectangle: fitz.Rect
    uri: str = ""
    page: int = -1
    point: fitz.Point | None = None
    zoom: float = 0.0
    file_path: str = ""


def page_link_specs(
    page: fitz.Page,
    *,
    rectangle_x_offset: float,
    selected_pages: int,
    destination_x_offsets: tuple[float, ...],
) -> tuple[LinkSpec, ...]:
    specs: list[LinkSpec] = []
    for raw_link in page.get_links():
        parsed = _parse_link(
            raw_link,
            rectangle_x_offset,
            selected_pages,
            destination_x_offsets,
        )
        if parsed is not None:
            specs.append(parsed)
    return tuple(specs)


def insert_link_specs(page: fitz.Page, specs: tuple[LinkSpec, ...]) -> None:
    for spec in specs:
        payload: dict[str, LinkValue] = {
            "kind": _fitz_kind(spec.link_type),
            "from": fitz.Rect(spec.rectangle),
        }
        if spec.uri:
            payload["uri"] = spec.uri
        if spec.page >= 0:
            payload["page"] = spec.page
        if spec.point is not None:
            payload["to"] = fitz.Point(spec.point)
        if spec.zoom:
            payload["zoom"] = spec.zoom
        if spec.file_path:
            payload["file"] = spec.file_path
        page.insert_link(payload)


def link_fingerprints(specs: tuple[LinkSpec, ...]) -> Counter[LinkFingerprint]:
    return Counter(_fingerprint(spec) for spec in specs)


def _parse_link(
    raw_link: dict,
    rectangle_x_offset: float,
    selected_pages: int,
    destination_x_offsets: tuple[float, ...],
) -> LinkSpec | None:
    kind = int(raw_link.get("kind", fitz.LINK_NONE))
    rectangle = fitz.Rect(raw_link.get("from", fitz.Rect()))
    rectangle.x0 += rectangle_x_offset
    rectangle.x1 += rectangle_x_offset
    if kind == fitz.LINK_URI:
        uri = str(raw_link.get("uri", "")).strip()
        return LinkSpec(LinkType.URI, rectangle, uri=uri) if uri else None
    if kind in (fitz.LINK_GOTO, fitz.LINK_NAMED):
        page_index = int(raw_link.get("page", -1))
        if page_index < 0 or page_index >= selected_pages:
            return None
        point = fitz.Point(raw_link.get("to", fitz.Point()))
        point.x += destination_x_offsets[page_index]
        return LinkSpec(
            LinkType.GOTO,
            rectangle,
            page=page_index,
            point=point,
            zoom=float(raw_link.get("zoom", 0.0)),
        )
    if kind == fitz.LINK_GOTOR:
        return LinkSpec(
            LinkType.GOTOR,
            rectangle,
            page=int(raw_link.get("page", -1)),
            point=fitz.Point(raw_link.get("to", fitz.Point())),
            zoom=float(raw_link.get("zoom", 0.0)),
            file_path=str(raw_link.get("file", "")),
        )
    if kind == fitz.LINK_LAUNCH:
        file_path = str(raw_link.get("file", "")).strip()
        unquoted_path = file_path.strip("\"'")
        if unquoted_path.lower().startswith(("http://", "https://")):
            return LinkSpec(LinkType.URI, rectangle, uri=unquoted_path)
        return LinkSpec(
            LinkType.LAUNCH,
            rectangle,
            file_path=file_path,
        )
    return None


def _fitz_kind(link_type: LinkType) -> int:
    match link_type:
        case LinkType.URI:
            return fitz.LINK_URI
        case LinkType.GOTO:
            return fitz.LINK_GOTO
        case LinkType.GOTOR:
            return fitz.LINK_GOTOR
        case LinkType.LAUNCH:
            return fitz.LINK_LAUNCH
        case unsupported:
            raise AssertionError(f"unsupported link type: {unsupported!r}")


def _fingerprint(spec: LinkSpec) -> LinkFingerprint:
    rectangle = tuple(_quarter(value) for value in spec.rectangle)
    match spec.link_type:
        case LinkType.URI:
            return (spec.link_type.value, *rectangle, spec.uri)
        case LinkType.GOTO | LinkType.GOTOR:
            point = spec.point or fitz.Point()
            return (
                spec.link_type.value,
                *rectangle,
                spec.page,
                _quarter(point.x),
                _quarter(point.y),
                _quarter(spec.zoom),
                spec.file_path,
            )
        case LinkType.LAUNCH:
            return (spec.link_type.value, *rectangle, spec.file_path)
        case unsupported:
            raise AssertionError(f"unsupported link type: {unsupported!r}")


def _quarter(value: float) -> int:
    return round(value * 4)
