from abc import ABC, abstractmethod


class BaseConnector(ABC):
    def __init__(self, org_id):
        if isinstance(org_id, bool) or not isinstance(org_id, int) or org_id <= 0:
            raise ValueError("A positive organization ID is required for every connector")
        self.org_id = org_id
        self._connected = False

    def _organization_credentials(self, integration_key):
        from app.api.v1.integrations import get_decrypted_credentials

        credentials = get_decrypted_credentials(self.org_id, integration_key)
        if not isinstance(credentials, dict):
            raise RuntimeError(f"No credentials are configured for organization integration: {integration_key}")
        return credentials

    @abstractmethod
    def connect(self):
        pass

    @abstractmethod
    def fetch_data(self, **kwargs):
        pass

    def test_connection(self):
        try:
            self.connect()
            return {"status": "ok", "connector": self.__class__.__name__}
        except Exception as e:
            return {"status": "error", "connector": self.__class__.__name__, "error": str(e)}
