# TurboLane Download Manager

RL-optimized multi-stream HTTP downloader using the TurboLane edge engine.

## Structure

```
turbolane-downloader/
├── turbolane/               ← TurboLane engine (edge mode)
│   ├── __init__.py
│   ├── engine.py            ← TurboLaneEngine(mode='client')
│   ├── policies/
│   │   ├── __init__.py
│   │   └── edge.py          ← EdgePolicy (public internet tuning)
│   └── rl/
│       ├── __init__.py
│       ├── agent.py         ← Q-learning agent (policy-agnostic)
│       └── storage.py       ← Q-table persistence
│
├── adapter.py               ← bridges engine ↔ downloader
├── downloader.py            ← MultiStreamDownloader (no RL logic)
├── simple_downloader.py     ← single-stream downloader
├── app.py                   ← Flask web interface
├── main.py                  ← tkinter GUI
├── config.py                ← app settings only
├── run.py                   ← entry point
├── models/edge/             ← persisted Q-table
└── requirements.txt
```

## How it works

```
app.py / main.py
    ↓
downloader.py          (no RL code — just calls adapter)
    ↓
adapter.py             (owns TurboLaneEngine instance)
    ↓
turbolane/engine.py    (mode='client' → EdgePolicy)
    ↓
turbolane/policies/edge.py   (edge-tuned reward + constraints)
    ↓
turbolane/rl/agent.py        (Q-learning)
turbolane/rl/storage.py      (Q-table persistence)
```

## Usage

```bash
pip install -r requirements.txt

# Web interface
python run.py

# GUI
python main.py
```

## RL Mode

When `use_rl=True`, the downloader asks the TurboLane engine how many
parallel streams to use at each monitoring interval (default: 5s).

The engine uses Q-learning tuned for public internet downloads:
- Optimal stream range: 6–10 parallel connections
- State: (throughput_level, rtt_level, loss_level)
- Actions: ±2, ±1, hold
- Reward: throughput improvement − loss penalty − stream overhead + optimal range bonus
