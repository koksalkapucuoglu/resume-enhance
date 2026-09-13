"""Request guards that belong to no single view."""

from django.conf import settings
from django.http import Http404, HttpResponsePermanentRedirect
from django.urls import reverse


class GoogleLoginAvailabilityMiddleware:
    """
    Answer 404, not 500, for Google sign-in URLs while Google is not configured.

    allauth registers the Google URLs whenever the provider app is installed.
    Without credentials, following one raises SocialApp.DoesNotExist — a server
    error for anyone who types the address, even though no page links to it.
    Once credentials are set, this steps aside entirely.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self._prefix = None

    def prefix(self):
        if self._prefix is None:
            # "/accounts/google/login/" -> "/accounts/google/"
            self._prefix = reverse("google_login").rsplit("login/", 1)[0]
        return self._prefix

    def __call__(self, request):
        if not settings.GOOGLE_LOGIN_ENABLED and request.path.startswith(self.prefix()):
            raise Http404("Google sign-in is not configured.")
        return self.get_response(request)


class CanonicalHostMiddleware:
    """
    Send www.<domain> to <domain>, keeping the path and query.

    Every absolute URL the app builds follows the host of the request. Served
    on both hosts, a Google sign-in started on www asked Google to return to
    https://www.<domain>/accounts/google/login/callback/, which is not a
    registered redirect URI, so Google refused with redirect_uri_mismatch.
    One host also keeps the session cookie in one place.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host()
        if host.startswith("www."):
            url = f"{request.scheme}://{host[4:]}{request.get_full_path()}"
            response = HttpResponsePermanentRedirect(url)
            if request.method not in ("GET", "HEAD"):
                response.status_code = 308  # a form post stays a post
            return response
        return self.get_response(request)
