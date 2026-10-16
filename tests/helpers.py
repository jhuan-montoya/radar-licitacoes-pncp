import json


class TransporteFalso:
    """Responde a uma fila de (status, headers, corpo) e registra as URLs chamadas."""

    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.urls = []

    def __call__(self, url, timeout):
        self.urls.append(url)
        resp = self.respostas.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


def json_resp(dados, status=200, headers=None):
    return status, headers or {}, json.dumps(dados).encode()
