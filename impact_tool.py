"""
Step: give the agent a way to check "who's actually watching right now"
so it can prioritize incidents by real-world impact, not just error severity.
 
This is intentionally simple — a small local JSON file, not a real analytics
system. That's fine for the MVP: it proves the concept (the agent factors in
business impact), which is the differentiator, not the data source itself.
"""
 
import json
import os
 
DATA_PATH = os.path.join(os.path.dirname(__file__), "live_titles.json")
 
def get_live_impact_for_region(region: str) -> dict:
    """Look up what's currently live in a given region and how many viewers
    / how much revenue is at stake, so an incident can be prioritized by
    real-world impact instead of just technical severity.
 
    Args:
        region: the region to check, e.g. "apac", "us-east", "eu-west"
 
    Returns:
        A dict describing the most-watched live title in that region,
        or a note that nothing is currently live there.
    """
    with open(DATA_PATH) as f:
        titles = json.load(f)
 
    region_titles = [t for t in titles if t["region"] == region]
    live_titles = [t for t in region_titles if t["is_live"]]
 
    if not live_titles:
        return {
            "region": region,
            "is_anything_live": False,
            "note": "No live content currently airing in this region — lower priority incident.",
        }
 
    top = max(live_titles, key=lambda t: t["current_viewers"])
    return {
        "region": region,
        "is_anything_live": True,
        "title": top["title"],
        "current_viewers": top["current_viewers"],
        "estimated_ad_revenue_per_minute_usd": top["estimated_ad_revenue_per_minute_usd"],
    }
 