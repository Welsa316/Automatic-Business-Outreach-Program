"""
scorer.py — Lead scoring engine.

Assigns a weighted score to each business based on website analysis,
business metadata, and category-specific checks.  Every point that
contributes to the total is recorded in a human-readable breakdown.
"""

import logging
from .config import (
    SCORE_WEIGHTS as W,
    REVIEW_THRESHOLDS,
    RATING_THRESHOLDS,
    CHAIN_KEYWORDS,
    RESTAURANT_KEYWORDS,
    SERVICE_KEYWORDS,
)
from .analyzer import SiteAnalysis
from .utils import normalize_text

logger = logging.getLogger("lead_engine")


def _is_chain(name: str) -> bool:
    """Heuristic: check if a business name looks like a major chain."""
    low = normalize_text(name)
    return any(kw in low for kw in CHAIN_KEYWORDS)


def _is_restaurant(categories: list[str]) -> bool:
    """Does the business look like a restaurant / food place?"""
    cats = " ".join(categories).lower()
    return any(kw in cats for kw in RESTAURANT_KEYWORDS)


def _is_service(categories: list[str]) -> bool:
    """Does the business look like a service provider?"""
    cats = " ".join(categories).lower()
    return any(kw in cats for kw in SERVICE_KEYWORDS)


def score_business(biz: dict, analysis: SiteAnalysis | None) -> dict:
    """
    Score a single business.

    Returns a dict with:
      - total_score (int)
      - breakdown   (list of {"reason": str, "points": int})
      - pitch_angle (str) — suggested outreach angle
    """
    breakdown: list[dict] = []
    pitch_angles: list[str] = []

    def add(reason: str, key: str) -> None:
        pts = W.get(key, 0)
        if pts:
            breakdown.append({"reason": reason, "points": pts})

    has_website = bool(biz.get("website"))

    # ------------------------------------------------------------------
    # 1. Website existence
    # ------------------------------------------------------------------
    if not has_website:
        add("No website listed", "no_website")
        pitch_angles.append("needs_new_website")
    elif analysis:
        # These only apply when NO_WEBSITE_ONLY is off and we're
        # also looking at businesses that have websites
        if analysis.is_social_only:
            add("Only a social media page (no real website)", "social_media_only")
            pitch_angles.append("needs_new_website")
        elif not analysis.reachable:
            add("Website is unreachable / broken", "site_unreachable")
            pitch_angles.append("site_broken")
        elif analysis.reachable:
            # Business has a working website — low priority
            add("Website looks modern and complete", "strong_website_penalty")
            pitch_angles.append("low_priority")

    # ------------------------------------------------------------------
    # 3. Business attractiveness bonuses
    # ------------------------------------------------------------------
    review_count = biz.get("review_count", 0)
    rating = biz.get("rating", 0.0)

    if review_count >= REVIEW_THRESHOLDS["very_high"]:
        add(f"{review_count} reviews (very popular)", "very_high_reviews_bonus")
    elif review_count >= REVIEW_THRESHOLDS["high"]:
        add(f"{review_count} reviews (popular)", "high_reviews_bonus")

    if rating >= RATING_THRESHOLDS["excellent"]:
        add(f"{rating} stars (excellent rating)", "excellent_rating_bonus")
    elif rating >= RATING_THRESHOLDS["good"]:
        add(f"{rating} stars (good rating)", "good_rating_bonus")

    # ------------------------------------------------------------------
    # 4. Chain penalty
    # ------------------------------------------------------------------
    if _is_chain(biz.get("business_name", "")):
        add("Suspected chain / franchise", "chain_penalty")
        pitch_angles.append("skip_chain")

    # ------------------------------------------------------------------
    # 5. Contact discovery bonuses
    # ------------------------------------------------------------------
    if biz.get("instagram"):
        add("Instagram profile found", "instagram_found")
    if biz.get("facebook"):
        add("Facebook page found", "facebook_found")
    if biz.get("tiktok"):
        add("TikTok profile found", "tiktok_found")
    if biz.get("email"):
        add("Email address found", "email_found")
    if biz.get("yelp"):
        add("Yelp listing found", "yelp_found")

    # ------------------------------------------------------------------
    # Compute total
    # ------------------------------------------------------------------
    total = sum(item["points"] for item in breakdown)
    total = max(total, 0)  # floor at 0

    # Pick primary pitch angle
    # Priority order: needs_new_website > site_broken > redesign > mobile > others
    angle_priority = [
        "needs_new_website", "site_broken", "redesign",
        "mobile_improvement", "speed_improvement", "cta_improvement",
        "contact_improvement", "content_improvement", "security_upgrade",
        "add_menu", "add_ordering", "add_booking", "low_priority",
        "skip_chain",
    ]
    primary_angle = "general_improvement"
    for a in angle_priority:
        if a in pitch_angles:
            primary_angle = a
            break

    return {
        "total_score": total,
        "breakdown": breakdown,
        "pitch_angle": primary_angle,
        "all_pitch_angles": list(dict.fromkeys(pitch_angles)),  # unique, ordered
    }


def score_all(businesses: list[dict],
              analyses: dict[int, SiteAnalysis]) -> list[dict]:
    """
    Score every business and attach results.

    Returns the same list of business dicts, each augmented with:
      lead_score, score_breakdown, pitch_angle, all_pitch_angles
    """
    for i, biz in enumerate(businesses):
        analysis = analyses.get(i)
        result = score_business(biz, analysis)
        biz["lead_score"] = result["total_score"]
        biz["score_breakdown"] = result["breakdown"]
        biz["pitch_angle"] = result["pitch_angle"]
        biz["all_pitch_angles"] = result["all_pitch_angles"]

        # Attach analysis summary too
        if analysis:
            biz["website_status"] = (
                "reachable" if analysis.reachable
                else ("social_only" if analysis.is_social_only
                      else ("unreachable" if biz.get("website") else "none"))
            )
            biz["detected_issues"] = analysis.detected_issues
            biz["site_title"] = analysis.title
            biz["final_url"] = analysis.final_url
            biz["response_time"] = analysis.response_time
        else:
            biz["website_status"] = "none" if not biz.get("website") else "not_checked"
            biz["detected_issues"] = []
            biz["site_title"] = ""
            biz["final_url"] = ""
            biz["response_time"] = 0

    # Sort by lead_score descending
    businesses.sort(key=lambda b: b["lead_score"], reverse=True)
    logger.info("Scoring complete. Top score=%d, Bottom score=%d",
                businesses[0]["lead_score"] if businesses else 0,
                businesses[-1]["lead_score"] if businesses else 0)
    return businesses
