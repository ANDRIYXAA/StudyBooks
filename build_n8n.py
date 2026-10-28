"""n8n workflow «Studybooks · Фон»: wakes the worker on the server.

  * every 5 minutes (retries after usage limits, picks up anything left over);
  * POST /webhook/studybooks-kick — the web page calls it right after an upload.

Both only run `sb.py kick`, which starts the worker in the background unless it is
already running. All logic lives in sb.py.

    python build_n8n.py OUT.json      -> for `n8n import:workflow`
"""

import json
import sys
import uuid

WID = "StudybooksBg0001"
SSH_CRED = {"sshPrivateKey": {"id": "pnFarlandsSsh01", "name": "server (n8n_key)"}}
KICK = "~/studybooks/.venv/bin/python ~/studybooks/sb.py kick"


def nid(name):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{WID}/{name}"))


nodes = [
    {"id": nid("every5"), "name": "Кожні 5 хв", "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2,
     "position": [0, 0], "parameters": {"rule": {"interval": [{"field": "minutes", "minutesInterval": 5}]}}},
    {"id": nid("hook"), "name": "Нове завантаження", "type": "n8n-nodes-base.webhook", "typeVersion": 2.1,
     "position": [0, 200], "webhookId": str(uuid.uuid5(uuid.NAMESPACE_URL, "studybooks-hook/kick")),
     "parameters": {"httpMethod": "POST", "path": "studybooks-kick", "responseMode": "onReceived", "options": {}}},
    {"id": nid("ssh"), "name": "SSH: kick", "type": "n8n-nodes-base.ssh", "typeVersion": 1,
     "position": [260, 100], "credentials": SSH_CRED,
     "parameters": {"authentication": "privateKey", "command": KICK}},
    {"id": nid("note"), "name": "Нотатка", "type": "n8n-nodes-base.stickyNote", "typeVersion": 1,
     "position": [-40, -200], "parameters": {"width": 520, "height": 170, "color": 7, "content":
        "## Studybooks\nБудить обробник навчальних матеріалів (`~/studybooks/sb.py kick`).\n"
        "Сайт: http://127.0.0.1:8810\n"
        "Уся логіка — у sb.py; тут лише розклад і вебхук. Генерується `build_n8n.py`."}},
]
wf = {"id": WID, "name": "Studybooks · Фон", "nodes": nodes,
      "connections": {"Кожні 5 хв": {"main": [[{"node": "SSH: kick", "type": "main", "index": 0}]]},
                      "Нове завантаження": {"main": [[{"node": "SSH: kick", "type": "main", "index": 0}]]}},
      "settings": {"executionOrder": "v1"}, "active": False, "pinData": {}}

with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(wf, f, ensure_ascii=False, indent=1)
