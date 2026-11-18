from ipaddress import ip_address
from urllib.parse import urlsplit


ALLOWED_MEDIA_HOSTS = {
    "teams": ("graph.microsoft.com", "sharepoint.com", "teams.microsoft.com"),
    "zoom": ("zoom.us",),
}


def is_allowed_media_url(value, source):
    if not isinstance(value, str) or len(value) > 4096:
        return False
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.port not in (None, 443):
            return False
        try:
            ip_address(host)
            return False
        except ValueError:
            pass
        domains = ALLOWED_MEDIA_HOSTS.get(source, ())
        return any(host == domain or host.endswith("." + domain) for domain in domains)
    except ValueError:
        return False
