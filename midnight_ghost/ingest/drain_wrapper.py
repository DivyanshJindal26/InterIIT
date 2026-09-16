from __future__ import annotations

import hashlib

from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig


def create_miner() -> TemplateMiner:
    config = TemplateMinerConfig()
    config.drain_sim_th = 0.4
    config.drain_depth = 4
    config.drain_max_children = 100
    config.drain_max_clusters = 1024
    return TemplateMiner(config=config)


def template_id_from_text(template_text: str) -> str:
    return hashlib.sha256(template_text.encode()).hexdigest()[:16]


def extract_params(message: str, template: str) -> dict:
    msg_tokens = message.split()
    tmpl_tokens = template.split()
    params = {}
    pi = 0
    for i, tt in enumerate(tmpl_tokens):
        if tt == '<*>':
            if i < len(msg_tokens):
                params[f'param_{pi}'] = msg_tokens[i]
            pi += 1
    return params


class DrainProcessor:
    def __init__(self) -> None:
        self._miners: dict[str, TemplateMiner] = {}

    def process(self, service: str, message: str) -> tuple[str, str, dict]:
        if service not in self._miners:
            self._miners[service] = create_miner()
        miner = self._miners[service]
        result = miner.add_log_message(message)
        template_text = result['template_mined']
        tid = template_id_from_text(template_text)
        params = extract_params(message, template_text)
        return tid, template_text, params
