"""Real Decisions calls on sample tabs (each costs ~0.0001 USD). Run: python test_decisions.py
Uses the host's own classify(); session labels come from M18_SESSIONS_JSON (a fake file is written here)."""
import json, os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
tmp = tempfile.mkdtemp()
fake = os.path.join(tmp, "sessions.json")
json.dump({"labels": {"a": "M18 PAD TABS", "b": "MINECRAFT WIKI", "c": "ELO RANKER"}}, open(fake, "w"))
os.environ["M18_SESSIONS_JSON"] = fake
sys.path.insert(0, os.path.join(HERE, "host"))
import m18_tabs_host as h
CASES = [
    ("YouTube lecture", "How Transformers Work - 3Blue1Brown - YouTube", "https://www.youtube.com/watch?v=abc", "video->later"),
    ("News article", "The quiet rise of heat pumps in cold countries | The Guardian", "https://www.theguardian.com/environment/2026/nov/heat-pumps", "article->later"),
    ("MDN docs", "Using the Fetch API - Web APIs | MDN", "https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API/Using_Fetch", "docs->keep"),
    ("Shop", "Mechanical keyboard 75% hot-swap - Amazon.com", "https://www.amazon.com/dp/B0XYZ", "shopping"),
    ("Web tool", "Google Cloud Console - Billing", "https://console.cloud.google.com/billing", "tool->keep"),
    ("Related to a session", "Chrome Extensions: Native Messaging | Chrome for Developers", "https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging", "related to M18 PAD TABS?"),
    ("Related (minecraft)", "Redstone Circuits - Minecraft Wiki", "https://minecraft.wiki/w/Redstone_circuits", "related to MINECRAFT WIKI"),
    ("Unrelated", "Best sourdough starter recipe - Serious Eats", "https://www.seriouseats.com/sourdough-starter", "article, not related"),
]
for name, title, url, expect in CASES:
    r = h.classify({"title": title, "url": url, "age_days": 6})
    print(f"{name:22} expect {expect:26} -> {r}")
