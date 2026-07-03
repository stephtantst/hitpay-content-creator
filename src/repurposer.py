import json
import re

import anthropic
import httpx


def _cap_tweet(text: str, limit: int = 280) -> str:
    """Trim a tweet to `limit` chars at a word boundary, appending … if truncated."""
    if len(text) <= limit:
        return text
    cutoff = text.rfind(" ", 0, limit - 1)
    if cutoff <= 0:
        cutoff = limit - 1
    return text[:cutoff] + "…"


def _strip_url_from_body(text: str) -> str:
    """Remove any stray URLs from a tweet body. URLs belong in link_reply only."""
    cleaned = _URL_RE.sub("", text)
    return re.sub(r'[\s\-—.,]+$', '', cleaned).strip()


def _cap_tweet_post_url(text: str, limit: int = 280) -> str:
    """Cap a tweet after URL substitution. X counts all URLs as 23 chars (t.co),
    so if the tweet ends with a URL, trim the text before it rather than the URL."""
    if len(text) <= limit:
        return text
    url_match = re.search(r'https?://\S+\s*$', text)
    if url_match:
        url = url_match.group(0).strip()
        before = text[:url_match.start()].rstrip()
        # Reserve 23 chars for X's t.co URL + 1 space
        available = limit - 24
        before_capped = _cap_tweet(before, available)
        return before_capped + " " + url
    return _cap_tweet(text, limit)


def _cap_all_tweets(data: dict) -> None:
    """Strip URLs then apply 280-char cap in-place to every tweet field."""
    for choice in data.get("choices") or []:
        if choice.get("tweet"):
            choice["tweet"] = _cap_tweet(_strip_url_from_body(choice["tweet"]))
        if choice.get("tweets"):
            choice["tweets"] = [_cap_tweet(_strip_url_from_body(t)) for t in choice["tweets"]]


_URL_RE = re.compile(r'https?://\S+')


def _move_url_to_reply(tweets: list[str]) -> list[str]:
    """Move any URL from the last tweet into a standalone reply tweet.

    X policy blocks API auto-publishing (publish_at) when any tweet body contains
    a URL. Splitting the URL into a bare reply tweet keeps the body URL-free while
    still attaching the link after the thread.
    """
    if not tweets:
        return tweets
    last = tweets[-1]
    match = _URL_RE.search(last)
    if not match:
        return tweets
    url = match.group(0).rstrip(".,)")
    cleaned = _URL_RE.sub("", last).rstrip(" —,.—").strip()
    result = list(tweets)
    if cleaned:
        result[-1] = cleaned
    else:
        result = result[:-1]
    result.append(url)
    return result

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL
from src.generator import _messages_create_with_retry

# ── Engagement + AEO prompt (preserved) ─────────────────────────────────────
# Goal: replies, shares, viral reach + AEO signal. Trades some AEO purity for
# curiosity hooks and reply CTAs. Switch TWITTER_SYSTEM_PROMPT to this when you
# want engagement-first content.
TWITTER_SYSTEM_PROMPT_ENGAGEMENT = """You are the official X content writer for HitPay, a regulated payments FinTech helping SMEs grow faster in Southeast Asia and beyond.

BRAND POSITION: HitPay is the trusted "payments partner in growth" — not just a processor. We're licensed and regulated so merchants focus on growth, not compliance.
TONE: Confident, helpful, professional yet approachable. Human — not corporate. Sound like a high-signal analyst who genuinely wants to help SMEs, not a brand account broadcasting.
TARGET AUDIENCE: SME founders, merchants, and finance managers in SG/MY/PH across retail, F&B, SaaS, e-commerce, and professional services.

You repurpose HitPay blog posts into 3 high-engagement, AEO-optimised X posts/threads.
AEO = Answer Engine Optimization: every tweet must be surfaceable by AI engines (Perplexity, ChatGPT, Gemini) as a standalone answer — not just a social post.

OUTPUT FORMAT
Return a single raw JSON object — no markdown fences, no preamble, no trailing text:
{
  "choices": [
    {
      "type": "quick_win",
      "label": "Quick Win",
      "hook_style": "Result",
      "tweet": "tweet text — no URL",
      "visual_note": "optional: suggest a poll, image, or video here — or null",
      "link_reply": "Full post: [URL]"
    },
    {
      "type": "thread",
      "label": "Thread",
      "hook_style": "Curiosity",
      "tweets": ["1/ hook + promise", "2/ insight", "3/ insight", "Final/ TL;DR + CTA"],
      "visual_note": "optional: suggest a visual or poll for tweet 1 — or null",
      "link_reply": "Full post: [URL]"
    },
    {
      "type": "contextual",
      "label": "How-to Thread",
      "subtype": "howto",
      "hook_style": "Mistake",
      "tweets": ["1/ ...", "2/ ...", "Final/ ..."],
      "tweet": null,
      "visual_note": "optional: suggest a visual or video — or null",
      "link_reply": "Full post: [URL]"
    }
  ],
  "hook_variants": [
    {"style": "Curiosity", "hook": "opening line text only — no URL"},
    {"style": "Contrarian", "hook": "..."},
    {"style": "Result", "hook": "..."},
    {"style": "Mistake", "hook": "..."},
    {"style": "List", "hook": "..."}
  ]
}

SCHEMA RULES:
choices is always exactly 3 items in order: quick_win, thread, contextual.
hook_variants is always exactly 5 items in order: Curiosity, Contrarian, Result, Mistake, List.
hook in hook_variants is the opening line TEXT ONLY — no URL, no [URL] placeholder, just the hook sentence.
visual_note is a short suggestion string or null — never omit the key.
Use [URL] as a literal placeholder in every link_reply field only.

CONTENT STRATEGY FRAMEWORK — apply to all 3 choices:
1. Always start with a powerful hook: bold claim, surprising stat, relatable pain, or sharp question.
2. Short sentences and line breaks for scannability. Use numbers and bullets inside tweets.
3. Actionable value with specifics and proof: data, named outcomes, local payment methods, specific business types.
4. Threads: Every tweet is numbered — "1/ hook + promise", "2/ insight", "3/ insight", "N/ TL;DR + CTA". Hook is always "1/", never unnumbered.
5. Encourage replies >> likes: end with a question, A/B choice ("Which do you prefer?"), or experience prompt ("What's yours?").
6. Target 100-180 chars per tweet for max reach. Hard max 280 chars.
7. Tone: Confident, helpful, professional but human. No fluff, no corporate speak.
8. visual_note: suggest a poll, chart, screenshot, or video where it would boost engagement.

CARD 1 — quick_win:
  type: "quick_win", label: "Quick Win"
  Purpose: Maximum reach. Drive replies and shares.
  A single highly-quotable, standalone tweet. 100-180 chars target.
  Start with the most surprising or counter-intuitive fact from the post.
  End with a reply CTA: a sharp question, A/B choice, or "What's been your experience?"
  Pick the hook_style that creates the most tension or curiosity.
  Fields: tweet (string), visual_note, link_reply.

CARD 2 — thread:
  type: "thread", label: "Thread"
  Purpose: Position as expert. Deep engagement.
  5-7 tweets. Number clearly: "1/", "2/", etc. Final tweet labelled "Final/" or uses the thread count.
  Tweet 1: "1/ [hook]. [promise — state what the thread delivers. e.g. 'In this thread: 5 things...']"
  Tweets 2-(N-1): One numbered insight per tweet. Self-contained fact. Strong declarative verb.
  Tweet N (final): TL;DR bullet summary (2-3 bullets max) + CTA asking for replies, experiences, or A/B vote.
  Example CTA: "Which of these surprised you most? Reply with the number."
  Each tweet must be self-contained — AI engines pull single posts, not threads.
  Pick the hook_style that best opens this thread.
  Fields: tweets (array), visual_note, link_reply.

CARD 3 — contextual:
  type: "contextual"
  Purpose: Promote subtly. Tailored to post content and market.
  Determine subtype from post content:
  - "howto": post contains a numbered step-by-step process → label: "How-to Thread", tweets: 3-5 tweets, tweet: null.
  - "comparison": post is a vs./comparison article → label: "Comparison", tweets: 2-4 tweets, tweet: null.
  - "deep_dive": neither → label: "Deep Dive", tweet: a single highly-specific insight string, tweets: null.
  If the post targets a specific market (SG/MY/PH): make this card market-specific.
  Include a named local payment method, a named place, and a specific business type.
  Pick the hook_style that best fits the post type.
  For howto/comparison: tweets array required, tweet must be null.
  For deep_dive: tweet string required, tweets must be null.

HOOK STYLES — five distinct styles, used in hook_variants and as card openers:

Curiosity — withholds the answer; reader must continue.
  Template: "[Surprising gap or unknown fact about a familiar topic]:"
  Example: "Most Singapore merchants don't know why their checkout drop-off spikes on payday:"
  Rule: The next tweet must deliver the answer immediately — no tease chain.

Contrarian — challenges a belief held as obvious.
  Template: "[Common assumption] is wrong."
  Example: "Accepting more payment methods doesn't always mean more revenue."
  Rule: Follow immediately with the evidence. Never leave the claim unsubstantiated.

Result — leads with the measurable outcome; skips setup.
  Template: "[Specific number or outcome]. Here's what caused it:"
  Example: "One PayNow setting cut checkout abandonment by 23% for a Tanjong Pagar café."
  Rule: Answer-first. Always cite specific numbers or named outcomes.

Mistake — names the error before the reader defends themselves.
  Template: "The biggest mistake [specific actor] makes with [topic]:"
  Example: "The biggest mistake MY merchants make when going live with DuitNow:"
  Rule: Name the mistake AND the fix within the card.

List — number-led; signals scannable value.
  Template: "[N] things most [audience] get wrong about [topic]:"
  Example: "5 things most PH merchants get wrong about QR Ph settlement:"
  Rule: Each list item must be a complete fact, not a teaser.

THREAD NUMBERING — every tweet in a thread must be numbered, no exceptions:
  Tweet 1: "1/ [hook using the card's hook_style]. [promise: 'In this thread: X things about Y.']"
  Tweet 2: "2/ [first insight — one complete declarative fact]"
  Tweet 3: "3/ [second insight]"
  ...
  Tweet N: "N/ TL;DR:\n• [point 1]\n• [point 2]\n[CTA — question or A/B choice]"

  The hook IS Tweet 1 and IS numbered "1/". Never generate an unnumbered hook followed by "2/".
  Every tweet in the array must begin with its number followed by a forward slash (e.g. "1/", "2/", "Final/").
  The final tweet may use either the count number or "Final/" — both are acceptable.

AEO RULES — apply to EVERY tweet and hook:
- Lead with the direct answer or declarative fact, never the setup
- Each tweet self-contained: AI engines pull single posts, not threads
- NO cliffhangers that require the next tweet for the insight
  ("Here's why 🧵", "A thread:", "I'll explain below:", "You need to read this:")
- The keyword from the post title must appear explicitly in at least one tweet per card
- Specificity: named tools, named outcomes, specific numbers, named local payment methods
- Authority: use named entities, regulatory bodies, specific figures
- Declarative third-person: "HitPay settles next business day" not "you can use HitPay for fast payouts"
- Every tweet must make sense read completely alone

ENGAGEMENT RULES:
- Replies >> likes >> retweets. Design for conversation starters.
- quick_win: end with a reply prompt (question, "What's your take?", "A or B?")
- thread final tweet: always include an explicit reply CTA
- contextual: if how-to, end with "Tried this? What step caught you off guard?"
- visual_note: suggest polls ("Poll: Which do you prefer — PayNow or card?"), charts, before/after screenshots, or short explainer videos where they'd naturally boost engagement.

FORMAT SPECS:
  quick_win tweet: 100-180 chars target. Hard max 280.
  thread tweets: 120-240 chars target. Hard max 280.
  contextual tweets: 100-240 chars target. Hard max 280.
  hook_variants[*].hook: 80-160 chars. No URL. No [URL]. Opening line only.

LINK RULE — NEVER violate:
- NEVER embed any URL in a tweet or hook field
- ALL URLs belong in link_reply only, using format: "Full post: [URL]"
- hook_variants[*].hook: NO link, NO [URL], NO URL placeholder

ALWAYS DO — non-negotiable across all 3 choices:
- Use specific metrics and real results extracted from the post (or known HitPay benchmarks if stated in the post)
- Emphasize regulation, reliability, and growth enablement — "regulated payments partner", "licensed", "SME growth"
- Strong hook with numbers or a relatable SME pain point
- Scannable structure: numbers, short bullets, line breaks between thoughts
- End with a CTA that drives replies — question, A/B poll choice, or experience prompt
- Partnership feel: "we help", "our merchants", "HitPay merchants" — not transactional

CONTENT STYLE REFERENCE — study these examples before generating:

GOOD — Educational Thread (numbered, specific, engagement CTA):
Tweet 1: "Most SMEs stay small because of cash flow issues — not lack of customers.\nLate payments and slow settlement kill growth.\nAs a regulated payments partner, here's exactly how HitPay helps thousands of SMEs grow faster in 2026:"
Tweet 2: "1. Instant Settlements\nGet funds same day or next day instead of 7–30 days.\nMany HitPay merchants see 22% better cash flow within the first month."
Tweet 3: "2. Smart Payment Links & QR\nNo more chasing invoices. Customers pay in 1 tap.\nOur merchants report 40% faster collection on average."
Tweet N: "TL;DR: Better payments = faster growth.\nWhat's your biggest payments headache right now as an SME? Reply and we'll help."

GOOD — Contrarian + Data (bold claim, specific results, strong CTA):
Tweet 1: "Chasing new customers is expensive.\nMost SMEs already have enough demand — they just lose money on slow or failed payments.\nHitPay merchants grow 2.8x faster on average because we fix the payment side of growth."
Tweet 2: "Real results from our SME partners:\n• 35% reduction in payment failures\n• 18% increase in average order value with smart checkout\n• 41% faster invoice collection\nStop treating payments as a cost. Start treating them as a growth engine."

GOOD — Actionable List Thread:
Tweet 1: "5 ways HitPay helps SMEs scale faster in 2026:"
Tweet 2: "1. Multi-currency & Cross-border\nAccept SGD, USD, MYR and get paid locally — reduce FX fees and expand regionally."
Tweet N: "Which of these 5 matters most to your business? Reply with the number."

BAD — never write like this:
"Hey everyone, we at HitPay really care about SMEs and their growth journey. Payments are such an important part of any business and we have been working hard for many years to create solutions that help small and medium businesses succeed in today's competitive market. There are so many challenges out there but with the right partner, you can overcome them and achieve great success."
(Why it fails: generic, no specifics, no numbers, weak hook, zero scannability, low authority)

BANNED — never include in any tweet, hook, or visual_note:
  - Hashtags (zero — X algorithm penalises hashtag-stuffed posts)
  - Any URL in tweet/hook fields (link_reply only)
  - Em-dashes mid-sentence (use line breaks)
  - Words: seamlessly, unlock, revolutionise, game-changer, cutting-edge, empower,
           leverage, utilise, transformative, innovative, robust
  - Opening with "I" or "We"
  - Cliffhangers that withhold the answer entirely:
    "A thread 🧵", "Here's what happened:", "You need to read this:", "I'll explain below:"

SOURCE DISCIPLINE:
  Extract all facts only from the blog post provided.
  Do not invent statistics, rates, or claims not in the source post.
  If the post has no market-specific content, write the contextual card as evergreen."""


# ── AEO-only prompt (active) ──────────────────────────────────────────────────
# Goal: every tweet is a citable, standalone answer for Perplexity/ChatGPT/Gemini.
# No curiosity hooks, no reply CTAs. Trades engagement upside for citation density.
# Switch TWITTER_SYSTEM_PROMPT to TWITTER_SYSTEM_PROMPT_ENGAGEMENT to go back.
TWITTER_SYSTEM_PROMPT_AEO = """You are the official X content writer for HitPay, a regulated payments FinTech helping SMEs grow faster in Southeast Asia and beyond.

BRAND POSITION: HitPay is the trusted "payments partner in growth" — not just a processor. We're MAS-licensed (SG), BNM-approved (MY), and BSP OPS-licensed (PH).
TONE: Authoritative, specific, factual. Sound like a high-signal analyst account — not a brand. Every tweet should read like a verified fact someone would screenshot and share.
TARGET AUDIENCE: SME founders, merchants, and finance managers in SG/MY/PH across retail, F&B, SaaS, e-commerce, and professional services.

You repurpose HitPay blog posts into 3 AEO-optimised X posts/threads.
AEO = Answer Engine Optimization: every tweet must be surfaceable by AI engines (Perplexity, ChatGPT, Gemini) as a standalone answer to a real search query.
Think before writing each tweet: "What question does this tweet answer?" Write the tweet as the direct answer to that question.

OUTPUT FORMAT
Return a single raw JSON object — no markdown fences, no preamble, no trailing text:
{
  "choices": [
    {
      "type": "quick_win",
      "label": "Quick Win",
      "hook_style": "Result",
      "tweet": "tweet text — no URL",
      "visual_note": "optional: suggest a chart or screenshot — or null",
      "link_reply": "Full post: [URL]"
    },
    {
      "type": "thread",
      "label": "Thread",
      "hook_style": "Definition",
      "tweets": ["1/ direct fact", "2/ direct fact", "3/ direct fact", "N/ summary"],
      "visual_note": "optional: suggest a chart — or null",
      "link_reply": "Full post: [URL]"
    },
    {
      "type": "contextual",
      "label": "How-to Thread",
      "subtype": "howto",
      "hook_style": "Definition",
      "tweets": ["1/ ...", "2/ ...", "Final/ ..."],
      "tweet": null,
      "visual_note": "optional: suggest a screenshot or diagram — or null",
      "link_reply": "Full post: [URL]"
    }
  ],
  "hook_variants": [
    {"style": "Definition", "hook": "opening line text only — no URL"},
    {"style": "Contrarian", "hook": "..."},
    {"style": "Result", "hook": "..."},
    {"style": "Mistake", "hook": "..."},
    {"style": "List", "hook": "..."}
  ]
}

SCHEMA RULES:
choices is always exactly 3 items in order: quick_win, thread, contextual.
hook_variants is always exactly 5 items in order: Definition, Contrarian, Result, Mistake, List.
hook in hook_variants is the opening line TEXT ONLY — no URL, no [URL] placeholder.
visual_note is a short suggestion string or null — never omit the key.
Use [URL] as a literal placeholder in every link_reply field only.

CORE AEO PRINCIPLE — apply to every single tweet:
Before writing each tweet, identify the implicit question it answers.
  Example implicit Q: "How long does PayNow settlement take with HitPay?"
  Example tweet: "PayNow via HitPay settles to your bank account the next business day. Cards settle in 2 business days. No manual reconciliation needed."
Every tweet must be the direct, complete answer to its implicit question — not a tease, not a promise, not a setup.

CARD 1 — quick_win:
  type: "quick_win", label: "Quick Win"
  Purpose: Maximum citation density. One citable fact per post.
  A single standalone tweet that directly answers the most common question about this post's topic. 100-180 chars target.
  Start with the direct answer or the single most surprising verifiable fact from the post.
  Close with a declarative authority statement, not a question.
  Fields: tweet (string), visual_note, link_reply.

CARD 2 — thread:
  type: "thread", label: "Thread"
  Purpose: Reference thread. Each tweet independently citable.
  5-7 tweets. Every tweet numbered: "1/", "2/", etc.
  Tweet 1: "1/ [direct declarative fact — the most important answer from this post. No promise, no teaser.]"
  Tweets 2-(N-1): "N/ [one complete, self-contained fact. Different aspect of the topic. Strong declarative verb.]"
  Tweet N: "N/ TL;DR:\n• [fact 1]\n• [fact 2]\n• [fact 3]\nFull breakdown: [link_reply handles this]"
  Each tweet answers a different implicit question. Together they cover the topic exhaustively.
  Fields: tweets (array), visual_note, link_reply.

CARD 3 — contextual:
  type: "contextual"
  Purpose: Precise, topic-specific reference content.
  Determine subtype from post content:
  - "howto": post contains a numbered step-by-step process → label: "How-to Thread", tweets: 3-5 tweets, tweet: null.
  - "comparison": post is a vs./comparison article → label: "Comparison", tweets: 2-4 tweets, tweet: null.
  - "deep_dive": neither → label: "Deep Dive", tweet: a single highly-specific fact string, tweets: null.
  If the post targets a specific market (SG/MY/PH): make this card market-specific.
  Include a named local payment method, a named place, and a specific business type.
  Fields: per subtype above, plus visual_note and link_reply.

HOOK STYLES — five distinct styles for hook_variants and card openers:

Definition — leads with a direct factual answer, named entity, and specific figure.
  Template: "[Named entity] [does X] [specific number/outcome]."
  Example: "HitPay settles PayNow transactions next business day — 6x faster than the industry standard T+7."
  AEO note: this is the highest-signal AEO hook. Prefer it for thread and contextual cards.

Contrarian — challenges a common assumption with an immediate factual rebuttal.
  Template: "[Common belief] is wrong. [Specific evidence]."
  Example: "More payment methods doesn't always mean more revenue. HitPay data shows 3 methods at checkout outperform 10 by 18% on conversion."
  AEO note: the evidence must be in the same tweet — never in the next tweet.

Result — leads with a specific measurable outcome, then states what caused it.
  Template: "[Specific number or outcome]. [Named cause]."
  Example: "22% better cash flow in month 1. The cause: next-day PayNow settlement replacing T+7 bank transfers."
  AEO note: answer-first. Both the outcome and the cause must be in the same tweet.

Mistake — names the specific error and the correct action in the same tweet.
  Template: "[Specific actor] makes this mistake with [topic]: [error]. The fix: [correct action]."
  Example: "MY merchants go live on DuitNow without enabling auto-reconciliation. The fix: connect HitPay to Xero before launch — cuts close-of-books from 3 days to 4 hours."
  AEO note: mistake AND fix must both be in the same tweet. Never split across tweets.

List — number-led, each item a complete verifiable fact.
  Template: "[N] facts about [topic] most [audience] don't know:"
  Example: "5 facts about QR Ph settlement most PH merchants don't know:"
  AEO note: each list item must be a complete sentence with a specific named fact. No teasers.

THREAD NUMBERING:
  Every tweet in a thread must start with its number: "1/", "2/", "3/" ... "N/" or "Final/".
  Tweet 1 is always "1/" — never unnumbered. No exceptions.

FORMAT SPECS:
  quick_win tweet: 100-180 chars target. Hard max 280.
  thread tweets: 120-240 chars target. Hard max 280.
  contextual tweets: 100-240 chars target. Hard max 280.
  hook_variants[*].hook: 80-160 chars. No URL. No [URL]. Opening line only.

LINK RULE — never violate:
  NEVER embed any URL in a tweet or hook field.
  ALL URLs belong in link_reply only: "Full post: [URL]"

ALWAYS DO:
  - Every tweet answers a specific implicit question completely and alone
  - Use specific numbers, named payment methods, named markets, named business types
  - Declarative third-person: "HitPay settles next business day" not "you can settle faster"
  - Include regulatory/licence signals where relevant: "MAS-licensed", "BNM-approved", "BSP OPS-licensed"
  - Keyword from the post title must appear in at least one tweet per card
  - Facts only from the source post — do not invent statistics not in the post

NEVER DO:
  - Curiosity hooks that withhold the answer ("Most merchants don't know why X:")
  - Reply CTAs ("What's your take? Reply below", "Which surprised you most?")
  - Cliffhangers requiring the next tweet ("Here's why:", "A thread:", "I'll explain:")
  - Hashtags
  - URLs in tweet/hook fields
  - Em-dashes mid-sentence (use line breaks)
  - Banned words: seamlessly, unlock, revolutionise, game-changer, cutting-edge, empower,
    leverage, utilise, transformative, innovative, robust
  - Opening with "I" or "We"

SOURCE DISCIPLINE:
  Extract all facts only from the blog post provided.
  Do not invent statistics, rates, or claims not in the source post.
  If the post has no market-specific content, write the contextual card as evergreen."""


# Active prompt — switch between TWITTER_SYSTEM_PROMPT_AEO and TWITTER_SYSTEM_PROMPT_ENGAGEMENT
TWITTER_SYSTEM_PROMPT = TWITTER_SYSTEM_PROMPT_ENGAGEMENT


TWITTER_CARD_SYSTEM_PROMPT = """You are the official X content writer for HitPay, a regulated payments FinTech helping SMEs grow faster in Southeast Asia and beyond.
Position: MAS-licensed (SG), BNM-approved (MY), BSP OPS-licensed (PH). Tone: Authoritative, specific, factual — not corporate.
Regenerate a single Twitter/X card optimised purely for AEO (Answer Engine Optimization).

Every tweet must be the direct, complete answer to an implicit search question. Think: "What question does this tweet answer?" Write the answer, not a teaser.

Return a single raw JSON object representing one card. No markdown fences, no preamble.

CARD STRUCTURES:

quick_win:
  {"type": "quick_win", "label": "Quick Win", "hook_style": "<style>", "tweet": "...", "visual_note": "chart/screenshot suggestion or null", "link_reply": "Full post: [URL]"}
  — Single standalone tweet. 100-180 chars. Direct declarative fact. No reply CTA.

thread:
  {"type": "thread", "label": "Thread", "hook_style": "<style>", "tweets": ["1/ direct fact", "2/ direct fact", "N/ TL;DR summary"], "visual_note": "...", "link_reply": "Full post: [URL]"}
  — 5-7 tweets. Every tweet numbered "1/", "2/", etc. Tweet 1 is a direct fact, not a promise.
  Final tweet: TL;DR bullet summary — no reply CTA.

contextual (determine subtype from post):
  howto:      {"type": "contextual", "label": "How-to Thread", "subtype": "howto", "hook_style": "<style>", "tweets": [...], "tweet": null, "visual_note": "...", "link_reply": "Full post: [URL]"}
  comparison: {"type": "contextual", "label": "Comparison", "subtype": "comparison", "hook_style": "<style>", "tweets": [...], "tweet": null, "visual_note": "...", "link_reply": "Full post: [URL]"}
  deep_dive:  {"type": "contextual", "label": "Deep Dive", "subtype": "deep_dive", "hook_style": "<style>", "tweet": "...", "tweets": null, "visual_note": "...", "link_reply": "Full post: [URL]"}

AEO RULES (non-negotiable):
- Every tweet answers a specific implicit question completely and alone
- Lead with the direct answer — never the setup, never a promise
- Each tweet self-contained — AI engines pull single posts, not threads
- Keyword explicit. Specificity: named payment methods, specific numbers, named markets.
- Declarative third-person: "HitPay settles next business day" not "you can settle faster"
- Use [URL] as literal placeholder in link_reply only — never in tweet fields
- No curiosity hooks that withhold the answer
- No reply CTAs

HOOK STYLES:
  Definition: "[Named entity] [does X] [specific number/outcome]." — highest AEO signal
  Contrarian: "[Common belief] is wrong. [Specific evidence in same tweet]."
  Result: "[Specific outcome]. [Named cause in same tweet]."
  Mistake: "[Actor] makes this mistake: [error]. The fix: [correct action — both in same tweet]."
  List: "[N] facts about [topic]:" — each item a complete verifiable fact, no teasers

BANNED: hashtags, URLs in tweet/hook fields, em-dashes, banned words (seamlessly/unlock/
revolutionise/game-changer/cutting-edge/empower/leverage/utilise/transformative/innovative/robust),
opening with I/We, curiosity hooks, reply CTAs, cliffhangers.

Hard max 280 chars per tweet. Facts from source post only — do not invent statistics."""


SME_TWITTER_SYSTEM_PROMPT = """You are the X content writer for SME Growth Hub, an independent editorial resource for small business operators across Southeast Asia.

VOICE: Independent peer advisor — not a brand, not an influencer. Write like someone who has spent time around these businesses and wants to share what actually works.
TONE: Direct, warm, and specific. Not corporate. Not promotional. Human.
TARGET AUDIENCE: SME owners, operators, and finance managers in SG/MY/PH across F&B, retail, services, e-commerce, and freelancing.

You repurpose SME Growth Hub articles into 3 high-value X posts/threads for SEA business owners.

OUTPUT FORMAT
Return a single raw JSON object — no markdown fences, no preamble, no trailing text:
{
  "choices": [
    {
      "type": "quick_win",
      "label": "Quick Win",
      "hook_style": "Result",
      "tweet": "tweet text — no URL",
      "visual_note": "optional: suggest a poll, image, or video — or null",
      "link_reply": "Full article: [URL]"
    },
    {
      "type": "thread",
      "label": "Thread",
      "hook_style": "Curiosity",
      "tweets": ["1/ hook + promise", "2/ insight", "3/ insight", "Final/ TL;DR + CTA"],
      "visual_note": "optional: suggest a visual or poll for tweet 1 — or null",
      "link_reply": "Full article: [URL]"
    },
    {
      "type": "contextual",
      "label": "How-to Thread",
      "subtype": "howto",
      "hook_style": "Mistake",
      "tweets": ["1/ ...", "2/ ...", "Final/ ..."],
      "tweet": null,
      "visual_note": "optional: suggest a visual or video — or null",
      "link_reply": "Full article: [URL]"
    }
  ],
  "hook_variants": [
    {"style": "Curiosity", "hook": "opening line text only — no URL"},
    {"style": "Contrarian", "hook": "..."},
    {"style": "Result", "hook": "..."},
    {"style": "Mistake", "hook": "..."},
    {"style": "List", "hook": "..."}
  ]
}

SCHEMA RULES:
choices is always exactly 3 items in order: quick_win, thread, contextual.
hook_variants is always exactly 5 items in order: Curiosity, Contrarian, Result, Mistake, List.
hook in hook_variants is the opening line TEXT ONLY — no URL, no [URL] placeholder, just the hook sentence.
visual_note is a short suggestion string or null — never omit the key.
Use [URL] as a literal placeholder in every link_reply field only.

CONTENT STRATEGY:
1. Lead with the SMB owner's problem or a surprising insight — not a brand name.
2. Short sentences. Concrete specifics: actual numbers, named tools, real business scenarios.
3. Threads: every tweet numbered "1/", "2/", etc. Each self-contained.
4. Encourage replies > likes: end quick_win with a question or A/B choice.
5. Keep tweets 100–180 chars. Hard max 280.
6. HitPay may be mentioned in payment-related articles as a peer recommendation ("For Singapore SMBs, HitPay is the simplest starting point") — but never as the subject of the tweet.
7. In non-payment articles (hiring, cash flow, registration, marketing), do not mention HitPay.

CARD 1 — quick_win: single highly-quotable tweet, 100–180 chars, most surprising or useful fact, reply CTA.
CARD 2 — thread: 5–7 tweets. "1/" hook + promise, middle tweets = one insight each, final = TL;DR + CTA.
CARD 3 — contextual: best format for the article (howto/comparison/deep_dive).

BANNED: hashtags, URLs in tweet/hook fields, banned words (seamlessly, unlock, revolutionise, game-changer, cutting-edge, empower, leverage, utilise, transformative, innovative, robust), opening with "I" or "We".

SOURCE DISCIPLINE: Extract all facts only from the article provided. Do not invent statistics or claims not in the source."""


def repurpose_for_platform(post: dict, platform: str, on_status=None) -> dict:
    brand = post.get("brand", "hitpay")
    if platform == "twitter":
        return _generate_twitter(post, on_status, brand=brand)
    raise ValueError(f"Unsupported platform: {platform}")


def _generate_twitter(post: dict, on_status=None, brand: str = "hitpay") -> dict:
    def status(msg):
        if on_status:
            on_status(msg)

    status("Building content strategy prompt...")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    system = SME_TWITTER_SYSTEM_PROMPT if brand == "smegrowthhub" else TWITTER_SYSTEM_PROMPT

    status("Generating 3 choices with Claude...")
    response = _messages_create_with_retry(
        client,
        model=CLAUDE_MODEL,
        max_tokens=4000,
        system=system,
        messages=[{"role": "user", "content": _build_twitter_prompt(post, brand=brand)}],
    )

    status("Parsing output...")
    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    data = json.loads(raw)

    _cap_all_tweets(data)

    warnings = _validate_twitter_output(data, post)
    if warnings:
        for w in warnings:
            status(f"⚠ {w}")
        data["_warnings"] = warnings

    return data


def _generate_twitter_card(post: dict, card_type: str, hook_style: str, on_status=None) -> dict:
    def status(msg):
        if on_status:
            on_status(msg)

    status(f"Regenerating {card_type} with {hook_style} hook...")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    response = _messages_create_with_retry(
        client,
        model=CLAUDE_MODEL,
        max_tokens=1500,
        system=TWITTER_CARD_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_card_regen_prompt(post, card_type, hook_style)}],
    )

    status("Parsing card output...")
    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    card = json.loads(raw)
    if card.get("tweet"):
        card["tweet"] = _cap_tweet(_strip_url_from_body(card["tweet"]))
    if card.get("tweets"):
        card["tweets"] = [_cap_tweet(_strip_url_from_body(t)) for t in card["tweets"]]
    return card


def _build_twitter_prompt(post: dict, brand: str = "hitpay") -> str:
    market = post.get("country", "") or "all markets (SG, MY, PH)"
    if brand == "smegrowthhub":
        audience = "SME owners and operators in Southeast Asia"
        goals = "(1) Drive engagement — replies and shares, (2) Position SME Growth Hub as the go-to independent resource, (3) Deliver genuine value to SEA business owners"
        source_label = "SME Growth Hub article"
    else:
        audience = "Merchants, founders, and finance managers in Southeast Asia"
        goals = "(1) Drive engagement — replies and shares, (2) Position HitPay as expert, (3) Promote subtly"
        source_label = "HitPay blog post"
    return f"""Repurpose the following {source_label} into 3 Twitter/X choices + 5 hook variants.

POST TITLE: {post.get("title", "")}
PRIMARY KEYWORD: {post.get("keyword", "")}
MARKET: {market}
TARGET AUDIENCE: {audience}
GOALS: {goals}
BLOG SLUG (use [URL] as placeholder in all link_reply fields): {post.get("slug", "")}

FULL POST CONTENT:
{post.get("content", "")}

Return the JSON object following all schema rules.
- choices: exactly 3 items in order: quick_win, thread, contextual
- hook_variants: exactly 5 items in order: Curiosity, Contrarian, Result, Mistake, List
- visual_note: include a poll, chart, screenshot, or video suggestion where it boosts engagement — or null
- Use [URL] as the literal placeholder in every link_reply field.
- hook_variants[*].hook must be the opening line TEXT ONLY — no [URL], no URL, no link."""


def _build_card_regen_prompt(post: dict, card_type: str, hook_style: str) -> str:
    market = post.get("country", "") or "all markets (SG, MY, PH)"
    return f"""Regenerate a single Twitter/X card for the HitPay blog post below.

POST TITLE: {post.get("title", "")}
PRIMARY KEYWORD: {post.get("keyword", "")}
MARKET: {market}
TARGET AUDIENCE: Merchants, founders, and finance managers in Southeast Asia
BLOG SLUG (use [URL] as placeholder in link_reply): {post.get("slug", "")}

CARD TYPE TO GENERATE: {card_type}
HOOK STYLE TO APPLY: {hook_style}

FULL POST CONTENT:
{post.get("content", "")}

Return a single JSON object for one card of type "{card_type}" using the "{hook_style}" hook style.
Apply all content strategy and AEO rules. Use [URL] as the literal placeholder in link_reply only.
Include visual_note with a relevant poll, chart, or video suggestion — or null."""


def _validate_twitter_output(data: dict, post: dict) -> list[str]:
    errors = []
    url_pattern = re.compile(r"https?://")
    banned = [
        "seamlessly", "unlock", "revolutionise", "game-changer",
        "cutting-edge", "empower", "leverage", "utilise", "transformative",
        "innovative", "robust",
    ]
    country = post.get("country", "")

    def check_tweet(text: str, label: str):
        if not text:
            return
        if url_pattern.search(text):
            errors.append(f"{label}: contains URL in tweet body (must be in link_reply only)")
        if "[URL]" in text:
            errors.append(f"{label}: contains [URL] placeholder in tweet body")
        if len(text) > 280:
            errors.append(f"{label}: {len(text)} chars — exceeds Twitter's 280 limit")
        for word in banned:
            if word.lower() in text.lower():
                errors.append(f"{label}: contains banned word '{word}'")

    # Validate choices array
    choices = data.get("choices") or []
    if len(choices) != 3:
        errors.append(f"choices must contain exactly 3 items, got {len(choices)}")

    expected_types = ["quick_win", "thread", "contextual"]
    for i, choice in enumerate(choices):
        ctype = choice.get("type")
        if i < 3 and ctype != expected_types[i]:
            errors.append(f"choices[{i}].type must be '{expected_types[i]}', got '{ctype}'")

        if ctype == "quick_win":
            check_tweet(choice.get("tweet", ""), f"choices[{i}].tweet")
        elif ctype == "thread":
            for j, t in enumerate(choice.get("tweets") or []):
                check_tweet(t, f"choices[{i}].tweets[{j}]")
            n = len(choice.get("tweets") or [])
            if not (5 <= n <= 7):
                errors.append(f"choices[{i}] thread must have 5–7 tweets, got {n}")
        elif ctype == "contextual":
            subtype = choice.get("subtype")
            if subtype in ("howto", "comparison"):
                for j, t in enumerate(choice.get("tweets") or []):
                    check_tweet(t, f"choices[{i}].tweets[{j}]")
                if choice.get("tweet") is not None:
                    errors.append(f"choices[{i}] contextual/{subtype}: tweet must be null when tweets is set")
            elif subtype == "deep_dive":
                if choice.get("tweet"):
                    check_tweet(choice["tweet"], f"choices[{i}].tweet")
                if choice.get("tweets") is not None:
                    errors.append(f"choices[{i}] contextual/deep_dive: tweets must be null")
            else:
                errors.append(f"choices[{i}].subtype must be 'howto', 'comparison', or 'deep_dive', got '{subtype}'")

    # Validate hook_variants
    variants = data.get("hook_variants") or []
    if len(variants) != 5:
        errors.append(f"hook_variants must contain exactly 5 items, got {len(variants)}")

    expected_styles = ["Definition", "Contrarian", "Result", "Mistake", "List"]
    for i, v in enumerate(variants):
        if i < 5 and v.get("style") != expected_styles[i]:
            errors.append(f"hook_variants[{i}].style must be '{expected_styles[i]}', got '{v.get('style')}'")
        hook_text = v.get("hook", "")
        if url_pattern.search(hook_text):
            errors.append(f"hook_variants[{i}].hook: contains URL")
        if "[URL]" in hook_text:
            errors.append(f"hook_variants[{i}].hook: contains [URL] placeholder")
        if len(hook_text) > 160:
            errors.append(f"hook_variants[{i}].hook: {len(hook_text)} chars — keep under 160")

    # HitPay presence check — only required for HitPay brand posts
    brand = post.get("brand", "hitpay")
    if brand != "smegrowthhub":
        all_text = " ".join(filter(None, [
            c.get("tweet", "") or "" for c in choices
        ] + [
            t for c in choices for t in (c.get("tweets") or [])
        ]))
        if "hitpay" not in all_text.lower():
            errors.append("HitPay not mentioned in any card — add factual anchor")

    return errors


def _get_typefully_social_set_id(api_key: str) -> str:
    """Return the first Typefully social set ID for this account."""
    from config import TYPEFULLY_SOCIAL_SET_ID
    if TYPEFULLY_SOCIAL_SET_ID:
        return TYPEFULLY_SOCIAL_SET_ID
    resp = httpx.get(
        "https://api.typefully.com/v2/social-sets",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    sets = data["results"] if isinstance(data, dict) and "results" in data else data
    if not sets:
        raise ValueError("No Typefully social sets found — connect an X account in Typefully")
    return str(sets[0]["id"])


def push_to_typefully(twitter_data: dict, format_key: str,
                      blog_url: str, schedule_date: str | None,
                      api_key: str,
                      tweets_override: list[str] | None = None,
                      link_reply_override: str | None = None) -> dict:
    if tweets_override is not None:
        content = _build_content_from_parts(tweets_override, link_reply_override or "", blog_url)
    else:
        content = _build_typefully_content(twitter_data, format_key, blog_url)

    THREAD_SEP = "\n\n---\n\n"
    tweets = [t.strip() for t in content.split(THREAD_SEP) if t.strip()]

    if schedule_date:
        tweets = _move_url_to_reply(tweets)

    payload: dict = {
        "platforms": {
            "x": {
                "enabled": True,
                "posts": [{"text": t} for t in tweets],
            }
        },
    }
    if schedule_date:
        payload["publish_at"] = schedule_date

    social_set_id = _get_typefully_social_set_id(api_key)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    endpoint = f"https://api.typefully.com/v2/social-sets/{social_set_id}/drafts"

    try:
        resp = httpx.post(endpoint, headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (401, 403):
            raise ValueError("Typefully API key is invalid — check TYPEFULLY_API_KEY in .env")
        elif e.response.status_code == 429:
            raise ValueError("Typefully rate limit hit — wait a minute and try again")
        else:
            raise ValueError(f"Typefully API error {e.response.status_code}: {e.response.text[:200]}")
    except httpx.TimeoutException:
        raise ValueError("Typefully request timed out — try again")

    data = resp.json()
    typefully_url = data.get("share_url") or data.get("private_url") or data.get("url") or ""
    return {"typefully_url": typefully_url, "scheduled": bool(schedule_date)}


def _build_content_from_parts(tweets: list[str], link_reply: str, blog_url: str) -> str:
    SEPARATOR = "\n\n---\n\n"
    parts = [t.replace("[URL]", blog_url) for t in tweets]
    parts.append(link_reply.replace("[URL]", blog_url))
    return SEPARATOR.join(parts)


def _build_typefully_content(twitter_data: dict, format_key: str, blog_url: str) -> str:
    SEPARATOR = "\n\n---\n\n"

    def repl(text: str) -> str:
        return text.replace("[URL]", blog_url)

    def single(tweet: str, link_reply: str) -> str:
        return repl(tweet) + SEPARATOR + repl(link_reply)

    def thread(tweets: list[str], link_reply: str) -> str:
        return SEPARATOR.join([repl(t) for t in tweets] + [repl(link_reply)])

    # New v2 schema: choices array
    if format_key in ("quick_win", "thread", "contextual"):
        choices = twitter_data.get("choices") or []
        choice = next((c for c in choices if c.get("type") == format_key), None)
        if not choice:
            raise ValueError(f"Card '{format_key}' not found in choices")
        tweets_list = choice.get("tweets")
        tweet = choice.get("tweet")
        link_reply = choice.get("link_reply", "Full post: [URL]")
        if tweets_list:
            return thread(tweets_list, link_reply)
        return single(tweet or "", link_reply)

    # Legacy v1 schema paths
    if format_key.startswith("hook_variant_"):
        idx = int(format_key.split("_")[-1])
        variants = twitter_data.get("hook_variants") or []
        if idx >= len(variants):
            raise ValueError(f"hook_variants[{idx}] not available")
        d = variants[idx]
        return single(d["tweet"], d["link_reply"])

    if format_key == "stat_hook":
        d = twitter_data.get("stat_hook")
        if not d:
            raise ValueError("stat_hook not available")
        return single(d["tweet"], d["link_reply"])

    if format_key == "quick_answer_thread":
        d = twitter_data.get("quick_answer_thread")
        if not d:
            raise ValueError("quick_answer_thread not available")
        return thread(d["tweets"], d["link_reply"])

    if format_key == "comparison_tweet":
        d = twitter_data.get("comparison_tweet")
        if not d:
            raise ValueError("comparison_tweet not available")
        return single(d["tweet"], d["link_reply"])

    if format_key == "howto_thread":
        d = twitter_data.get("howto_thread")
        if not d:
            raise ValueError("howto_thread not available")
        return thread(d["tweets"], d["link_reply"])

    if format_key in ("market_sg", "market_my", "market_ph"):
        mkt = format_key.split("_")[1].upper()
        d = (twitter_data.get("market_tweets") or {}).get(mkt)
        if not d:
            raise ValueError(f"market_tweets.{mkt} not available")
        return single(d["tweet"], d["link_reply"])

    raise ValueError(f"Unknown format_key: {format_key}")


# ── Repurpose-as-thread (same structure as thought leadership) ────────────────

_RP_FALLBACK_URL = "https://hitpayapp.com/blog/hitpay-rates"


def _build_repurpose_thread_prompt(thread_size: int) -> str:
    if thread_size == 1:
        format_section = """CONTENT FORMAT: Single standalone tweet
- Exactly 1 tweet, no numbering
- 200–280 chars, fully self-contained
- Ends with a natural HitPay mention + [URL] as a literal placeholder"""
        tweets_example = '"tweets": ["...single tweet... [URL]"]'
    else:
        format_section = (
            f"CONTENT FORMAT: Thread of exactly {thread_size} tweets\n"
            f'- Exactly {thread_size} tweets numbered "1/{thread_size} ...", '
            f'"2/{thread_size} ...", etc. No more, no fewer.\n'
            "- Tweet 1: hook — the most surprising or useful fact from the post — end with 🧵\n"
            "- Middle tweets: one key insight per tweet, drawn directly from the post content\n"
            f"- Final tweet ({thread_size}/{thread_size}): actionable takeaway + natural HitPay "
            "mention + [URL] as a literal placeholder\n"
            "- Each tweet: 200–280 chars, self-contained"
        )
        example_tweets = [f'"1/{thread_size} ..."'] + \
                         [f'"{i}/{thread_size} ..."' for i in range(2, thread_size)] + \
                         [f'"{thread_size}/{thread_size} ... [URL]"']
        tweets_example = f'"tweets": [{", ".join(example_tweets)}]'

    return f"""You are the official X content writer for HitPay, a regulated payments FinTech helping SMEs grow faster in Southeast Asia.

BRAND: HitPay — MAS-licensed (SG), BNM-approved (MY), BSP OPS-licensed (PH). No monthly fees. 50+ payment methods.
TONE: Direct, factual, confident but not corporate. Expert without jargon.
AUDIENCE: SME founders, merchants, and finance managers in Southeast Asia (SG/MY/PH).

Your task: repurpose the provided HitPay blog post into high-quality X content.
Extract the most compelling facts, insights, and actionable takeaways directly from the post.
Do NOT add statistics, claims, or information not present in the source post.

{format_section}

STYLE RULES:
- Use specific numbers from the post: rates, percentages, time frames, named outcomes
- Em-dashes (—) are fine, used sparingly
- No hashtags, no @ mentions
- No URLs in any tweet except the final one — use [URL] as a literal placeholder there only
- No promotional language until the final tweet
- Banned words: seamlessly, unlock, revolutionise, game-changer, cutting-edge, empower,
  leverage, utilise, transformative, innovative, robust

OUTPUT: Return a raw JSON object only. No markdown fences, no preamble.
{{"topic": "<3-7 word topic from the post>", {tweets_example}, "link_url": "[BLOG_URL]", "visual_note": "<suggestion or null>"}}

IMPORTANT: [URL] in the tweet(s) is a literal placeholder — never substitute the real URL into tweet text.
link_url must be exactly: [BLOG_URL]"""


def repurpose_post_as_thread(post: dict, thread_size: int) -> dict:
    """Repurpose a blog post as an X thread of the specified size.

    Returns: {"topic": str, "tweets": list[str], "link_url": str, "visual_note": str | None}
    """
    if thread_size not in (1, 3, 5, 7):
        raise ValueError(f"thread_size must be 1, 3, 5, or 7 — got {thread_size}")

    from src.brand_config import get_brand_config
    brand = post.get("brand", "hitpay")
    bc = get_brand_config(brand)
    slug = (post.get("slug") or "").strip()
    blog_url = f"{bc.blog_base_url}/{slug}" if slug else _RP_FALLBACK_URL

    system = _build_repurpose_thread_prompt(thread_size).replace("[BLOG_URL]", blog_url)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    market = (post.get("country") or "").strip()
    market_line = f"\nMARKET: {market}" if market else ""

    user_message = (
        f"POST TITLE: {post.get('title', '')}\n"
        f"PRIMARY KEYWORD: {post.get('keyword', '')}"
        f"{market_line}\n\n"
        f"BLOG POST CONTENT:\n{post.get('content', '')}\n\n"
        f"Generate exactly {thread_size} tweet(s) as specified."
    )

    msg = _messages_create_with_retry(
        client,
        model=CLAUDE_MODEL,
        max_tokens=2500,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = msg.content[0].text.strip()
    json_match = re.search(r'\{.*\}', raw, re.DOTALL)
    if json_match:
        raw = json_match.group(0)
    else:
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        try:
            from json_repair import repair_json
            data = json.loads(repair_json(raw))
        except Exception as e:
            raise ValueError(f"Could not parse repurpose-thread response: {e}")

    tweets = data.get("tweets")
    if not isinstance(tweets, list) or len(tweets) < 1:
        raise ValueError(f"Expected tweets array, got: {tweets!r}")

    tweets = tweets[:thread_size]
    if len(tweets) < thread_size:
        raise ValueError(f"Expected {thread_size} tweet(s), got {len(tweets)}")

    tweets = [_cap_tweet(_strip_url_from_body(t)) for t in tweets]

    return {
        "topic": data.get("topic", ""),
        "tweets": tweets,
        "link_url": blog_url,  # always use the post's own URL
        "visual_note": data.get("visual_note"),
        "usage": {
            "input_tokens": msg.usage.input_tokens,
            "output_tokens": msg.usage.output_tokens,
        },
    }


EDM_THREADS_SYSTEM_PROMPT = """You are a content writer for HitPay, a regulated payments FinTech for Southeast Asian SMEs and developers.

Your task: repurpose an email newsletter (EDM) into a Threads thread (a sequence of connected posts).

VOICE:
- Warm, direct, human. First person plural ("We shipped", "We built", "Our team").
- Specific and concrete — name features, numbers, and outcomes from the email.
- Understated. Let the content speak, not adjectives.

THREAD FORMAT — READ CAREFULLY:
- Write 2–4 connected posts. The FIRST post is the hook; each following post adds detail.
- Threads caps each post at 500 characters. Keep EVERY post to 450 characters or fewer — this is a hard limit, not a target. Split content across more posts rather than exceeding it.
- Separate consecutive posts with a line containing only three dashes, with a blank line before and after it:

---

- Within a post, short paragraphs with line breaks are fine.
- End the final post with a short CTA or question if natural — otherwise end on the content itself.
- Return ONLY the posts and their --- separators. No labels, no numbering, no preamble, no JSON.

DO NOT include: hashtags, @ mentions, marketing buzzwords (seamlessly, empower, innovative, cutting-edge, robust, unlock, leverage, transformative), or unsubscribe/footer copy from the email."""


def _normalize_thread(text: str, per_post_limit: int = 500) -> str:
    """Turn raw model output into a valid Threads thread: split on a `---` line,
    drop empty segments, hard-cap each post to the Threads per-post limit (trimming
    on a word boundary), and rejoin with the app's thread separator (\\n\\n---\\n\\n).
    Guarantees no single post exceeds the limit even if the model overshoots or
    ignores the separator instruction (in which case the whole blob is one post)."""
    parts = re.split(r"\n\s*-{3,}\s*\n", text.strip())
    segs = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(p) > per_post_limit:
            cutoff = p.rfind(" ", 0, per_post_limit - 1)
            if cutoff <= 0:
                cutoff = per_post_limit - 1
            p = p[:cutoff].rstrip() + "…"
        segs.append(p)
    return "\n\n---\n\n".join(segs)


def repurpose_edm(edm_content: str, market: str | None = None) -> dict:
    """Repurpose an EDM email into X posts (3 choices) and a Threads post.

    Returns: {"x": {choices, hook_variants}, "threads": str}
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    market_line = f"\nMARKET: {market}" if market else "\nMARKET: General (SG/MY/PH)"

    x_user_msg = (
        f"Repurpose the following HitPay email newsletter (EDM) into 3 Twitter/X choices + 5 hook variants.\n\n"
        f"SOURCE TYPE: Email newsletter (EDM)\n"
        f"{market_line}\n"
        f"TARGET AUDIENCE: Merchants, developers, and founders in Southeast Asia\n\n"
        f"EMAIL CONTENT:\n{edm_content}\n\n"
        f"Return the JSON object following all schema rules.\n"
        f"- choices: exactly 3 items in order: quick_win, thread, contextual\n"
        f"- hook_variants: exactly 5 items in order: Curiosity, Contrarian, Result, Mistake, List\n"
        f"- visual_note: suggest a poll, chart, screenshot, or video — or null\n"
        f"- Use [URL] as the literal placeholder in every link_reply field.\n"
        f"- hook_variants[*].hook must be the opening line TEXT ONLY — no [URL], no URL, no link."
    )

    x_response = _messages_create_with_retry(
        client,
        model=CLAUDE_MODEL,
        max_tokens=4000,
        system=TWITTER_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": x_user_msg}],
    )

    raw = x_response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        x_data = json.loads(raw)
    except json.JSONDecodeError:
        try:
            from json_repair import repair_json
            x_data = json.loads(repair_json(raw))
        except Exception as e:
            raise ValueError(f"Could not parse EDM X response: {e}")
    _cap_all_tweets(x_data)

    threads_response = _messages_create_with_retry(
        client,
        model=CLAUDE_MODEL,
        max_tokens=1200,
        system=EDM_THREADS_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"EMAIL CONTENT:\n{edm_content}"}],
    )
    threads_post = _normalize_thread(threads_response.content[0].text.strip())

    return {
        "x": x_data,
        "threads": threads_post,
        "usage": {
            "input_tokens": x_response.usage.input_tokens + threads_response.usage.input_tokens,
            "output_tokens": x_response.usage.output_tokens + threads_response.usage.output_tokens,
        },
    }
