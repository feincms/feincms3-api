"""HTTP for the content API, with the standard library only"""

import json
import mimetypes
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid


class APIError(Exception):
    def __init__(self, status, data):
        super().__init__(status, data)
        self.status = status
        self.data = data


class Client:
    def __init__(self, url, token):
        self.url = url.rstrip("/") + "/"
        self.token = token

    def request(self, method, path, *, params=None, body=None, headers=None):
        url = self.url + path.lstrip("/")
        if params := {
            key: value for key, value in (params or {}).items() if value is not None
        }:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        headers = {"Accept": "application/json", **(headers or {})}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        return self._send(urllib.request.Request(url, data, headers, method=method))

    def upload(self, path):
        boundary = uuid.uuid4().hex
        name = os.path.basename(path)
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            content = f.read()
        data = b"".join(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode(),
                f"Content-Type: {content_type}\r\n\r\n".encode(),
                content,
                f"\r\n--{boundary}--\r\n".encode(),
            ]
        )
        headers = {
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            self.url + "uploads/", data, headers, method="POST"
        )
        return self._send(request)

    def _send(self, request):
        try:
            with urllib.request.urlopen(request) as response:
                content = response.read()
                return response.status, json.loads(content) if content else None
        except urllib.error.HTTPError as exc:
            content = exc.read()
            try:
                data = json.loads(content)
            except ValueError:
                data = {"detail": content.decode(errors="replace") or exc.reason}
            raise APIError(exc.code, data) from None
