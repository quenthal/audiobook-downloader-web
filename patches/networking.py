from audiobookdl import AudiobookFile, exceptions, logging
from audiobookdl.utils.audiobook import AESEncryption

from typing import Dict, List
import json
import os
import m3u8
import requests


def post(self, url: str, **kwargs) -> bytes:
    """Make post request with `Source` session"""
    resp = self._session.post(url, **kwargs)
    if resp.status_code == 200:
        return resp.content
    logging.debug(f"Failed to download data from: {url}\nResponse:\n{resp.content}")
    raise exceptions.RequestError


def get(self, url: str, force_cookies: bool = False, **kwargs) -> bytes:
    """Make get request with `Source` session"""
    if force_cookies:
        resp = self._session.get(
            url,
            cookies=_get_all_cookies(self._session),
            **kwargs
        )
    else:
        resp = self._session.get(url, **kwargs)
    if resp.status_code == 200:
        return resp.content
    logging.debug(f"Failed to download data from: {url}\nResponse:\n{resp.content}")
    raise exceptions.RequestError


def post_json(self, url: str, **kwargs) -> dict:
    """Downloads data with the given url and converts it to json"""
    resp = self.post(url, **kwargs)
    return json.loads(resp.decode('utf8'))


def get_json(self, url: str, **kwargs) -> dict:
    """Downloads data with the given url and converts it to json"""
    resp = self.get(url, **kwargs)
    return json.loads(resp.decode('utf8'))


def get_stream_files(self, url: str, headers={}, extension=None) -> List[AudiobookFile]:
    """Create audio files from an HLS master or media playlist."""

    playlist = m3u8.load(url, headers=headers)

    # A master playlist contains variant playlists rather than segments.
    if playlist.is_variant:
        if not playlist.playlists:
            raise exceptions.RequestError

        # Prefer the lowest-bandwidth variant. Nextory URLs currently request
        # quality=low, and audiobook speech does not benefit from a larger
        # variant.
        variant = min(
            playlist.playlists,
            key=lambda item: (
                item.stream_info.bandwidth
                if item.stream_info.bandwidth is not None
                else float("inf")
            )
        )

        return get_stream_files(
            self,
            variant.absolute_uri,
            headers=headers,
            extension=extension
        )

    files = []

    for seg in playlist.segments:
        segment_extension = extension

        if segment_extension is None:
            segment_extension = (
                os.path.splitext(seg.absolute_uri)[1][1:]
                .split("?")[0]
            )

        current = AudiobookFile(
            url=seg.absolute_uri,
            ext=segment_extension,
            headers=headers
        )

        if seg.key and seg.key.method != "NONE":
            current.encryption_method = AESEncryption(
                key=self._get_page(
                    seg.key.absolute_uri,
                    headers=headers
                ),
                iv=int(seg.key.iv, 0).to_bytes(
                    16,
                    byteorder="big"
                )
            )

        files.append(current)

    return files

def _get_all_cookies(session: requests.Session) -> Dict[str, str]:
    """
    Retrieves all cookies from session

    :returns: Dictionary of cookies
    """
    cookies = {}
    for cookie in session.cookies:
        cookies[cookie.name] = str(cookie.value)
    return cookies
