"""NovaMart — the AI agent orchestrator, and the console a visitor sees.

Six pages, not thirteen. The earlier version had one page per subsystem, which
is how the people who built it think about it and not how a first-time reader
does: cost, reliability, drift and evaluation each had a page, so understanding
the product meant opening ten of them. They are still here, as sections inside
the page whose question they answer.

The rule this file is arranged around: **the complexity belongs in the system,
not on the first screen.** A visitor should be able to say what this is, what it
does and what they can try within a minute; everything deeper sits one
disclosure away.

Nothing here holds authority. Every answer comes from ``AgentPlatform.run()``,
the same call the CLI and the HTTP API make, and every number is read from the
repository or the simulated dataset rather than written down as a claim.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sys
from dataclasses import replace
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Final

import altair as alt
import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import demo_budget
import demo_content as demo
import execution_view
import overview as overview_page
from i18n import (
    DEFAULT_LOCALE,
    LOCALE_KEY,
    LOCALE_LABELS,
    LOCALES,
    current_locale,
    t,
)

from agent_platform.config import DEFAULT_MAX_QUESTION_CHARS, Settings
from agent_platform.cost.pricing import pricing_notice
from agent_platform.guardrails.authorization import describe_matrix
from agent_platform.guardrails.rules import describe_rules
from agent_platform.i18n import use_locale
from agent_platform.llm.authorization import LiveNotAuthorised
from agent_platform.observability.metrics import collect_metrics
from agent_platform.platform import AgentPlatform
from agent_platform.tools.fake_tools import WorkingSet, dataset_scope

st.set_page_config(
    page_title=f"{demo.PRODUCT_NAME} — {demo.PRODUCT_TAGLINE}",
    page_icon=":material/hub:",
    layout="wide",
    initial_sidebar_state="auto",
)

# One constant stylesheet, injected once.
#
# It is a single literal that never varies between reruns, so React sees byte
# identical markup every time and has nothing to reconcile. That matters here:
# the previous version of this app produced `insertBefore` errors, and the way
# to keep the fix is to make sure the only raw markup in the file is static.
#
# Colour carries meaning and nothing else. Blue is the product, green is
# ALLOW/healthy, red is DENY/blocked, amber is a warning. Nothing is coloured
# for decoration, so a colour in this interface is always information.
_STYLE = """
<style>
  /* ===================================================== tokens
     Colour, radius, type and borders come from `.streamlit/config.toml`, which
     is where Streamlit's own widgets read them. These are the few values that
     only this stylesheet uses, mirrored so a rule below can name them. */
  :root {
    --ap-line:    #1E2733;
    --ap-line-2:  #263141;
    --ap-muted:   #8B98A9;
    --ap-dim:     #64707F;
    --ap-text:    #E6EDF3;
    --ap-blue:    #2F81F7;
    --ap-blue-2:  #5AA0FF;
    --ap-cyan:    #38BDF8;
    --ap-green:   #3FB950;
    --ap-red:     #F85149;
    --ap-amber:   #D29922;
    --ap-violet:  #A371F7;
    --ap-surface: #0E141D;
    --ap-raised:  #131A25;
    --ap-ground:  #0A0D13;
  }

  /* ===================================================== page rhythm */
  /* 1180 was sized for prose. The architecture scene is the widest thing
     the dashboard renders and was being squeezed into 705px; the extra
     140 goes to the diagram. Prose does not spread with it -- `.ap-lede`
     is capped at 62ch, which is where it was already. */
  .block-container { padding-top: 2.2rem; max-width: 1320px; }
  h1, h2, h3, h4 { letter-spacing: -0.02em; }
  [data-testid="stMain"] h1 { font-size: clamp(1.8rem, 3.2vw, 2.5rem) !important; }

  /* An h2 is a section rule here, not a second headline: small, spaced above,
     and set off by a hairline that runs the width of the content. That single
     change is most of what makes six pages read as one product. */
  [data-testid="stMain"] h2 {
    margin-top: 2.6rem; padding-top: 0.9rem;
    border-top: 1px solid var(--ap-line);
    font-size: 1.02rem !important; font-weight: 600;
    letter-spacing: 0.01em; color: var(--ap-text);
  }
  [data-testid="stMain"] h3 { margin-top: 1.4rem; font-size: 0.95rem !important; }
  hr { border-color: var(--ap-line); }

  /* Sections fade in rather than snapping. 160ms is below the threshold where
     motion starts to feel like an effect. */
  @media (prefers-reduced-motion: no-preference) {
    .ap-fade { animation: ap-in 0.16s ease-out both; }
  }
  @keyframes ap-in { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; } }

  .ap-eyebrow {
    font-size: 0.7rem; letter-spacing: 0.16em; text-transform: uppercase;
    color: var(--ap-blue-2); margin-bottom: 0.5rem; font-weight: 600;
  }
  .ap-lede { color: var(--ap-muted); font-size: 1rem; max-width: 62ch; line-height: 1.6; }

  /* ===================================================== primitives */

  /* A bordered surface. Everything that is "a thing on the page" sits in one,
     which is what stops each page inventing its own container. */
  .ap-panel {
    border: 1px solid var(--ap-line); background: var(--ap-surface);
    border-radius: 8px; padding: 1rem 1.15rem;
  }

  /* The rounded square that holds a glyph. Enterprise interfaces mark a
     concept with one; a bare glyph in running text reads as a typo. */
  .ap-tile {
    display: inline-flex; align-items: center; justify-content: center;
    width: 30px; height: 30px; border-radius: 7px; flex: 0 0 30px;
    border: 1px solid var(--ap-line-2); background: var(--ap-raised);
    color: var(--ap-blue-2); font-size: 0.86rem; font-style: normal;
  }

  /* A small factual label. Not a button: it never does anything. */
  .ap-chip {
    display: inline-block; font-size: 0.68rem; letter-spacing: 0.04em;
    color: var(--ap-muted); border: 1px solid var(--ap-line);
    background: var(--ap-raised); border-radius: 4px; padding: 0.16rem 0.45rem;
  }

  .ap-card {
    border: 1px solid var(--ap-line); background: var(--ap-surface);
    border-radius: 8px; padding: 1rem 1.1rem 0.95rem; height: 100%;
    display: flex; flex-direction: column; gap: 0.55rem;
    transition: border-color 0.18s ease, background 0.18s ease;
  }
  .ap-card:hover { border-color: var(--ap-line-2); background: var(--ap-raised); }
  .ap-card h4 {
    margin: 0; font-size: 0.94rem; font-weight: 600; color: var(--ap-text);
    letter-spacing: -0.01em;
  }
  .ap-card p  { margin: 0; color: var(--ap-muted); font-size: 0.85rem; line-height: 1.55; }

  /* Streamlit puts `margin-bottom: -16px` on every `stMarkdownContainer`, to
     cancel the 16px a trailing `<p>` carries of its own. A card is a bordered
     `<div>` with `margin: 0`, so there is nothing to cancel and the rule
     over-subtracts: measured, the container reported 133px while the card drew
     149, and the missing 16 were exactly the gap that should have separated
     the row from whatever came next. Every card row on the page was drawing
     into its neighbour. This does not hide the overflow -- it stops removing a
     margin that was never there. */
  [data-testid="stMarkdownContainer"]:has(> .ap-card) { margin-bottom: 0; }

  /* With the row measuring its true height, a little more than the 16px block
     gap so the cards read as their own band rather than as the top of the next
     one. */
  [data-testid="stHorizontalBlock"]:has(.ap-card) { margin-bottom: 0.5rem; }

  /* The counter strip under the hero. One row of facts the page can
     prove: every number is read from the code at render time, none is typed
     into a string. */
  .ap-counters {
    display: flex; flex-wrap: wrap; border: 1px solid var(--ap-line);
    border-radius: 8px; background: var(--ap-surface); overflow: hidden;
    margin: 0.2rem 0 0.4rem;
  }
  .ap-counter {
    flex: 1 1 200px; padding: 1rem 1.15rem;
    border-left: 1px solid var(--ap-line);
    display: flex; gap: 0.8rem; align-items: flex-start;
  }
  .ap-counter:first-child { border-left: none; }
  .ap-counter b {
    font-size: 1.5rem; font-weight: 650; letter-spacing: -0.03em;
    line-height: 1.15; color: var(--ap-text);
  }
  .ap-counter .ap-unit {
    color: var(--ap-muted); font-size: 0.82rem; margin-left: 0.35rem;
    font-weight: 400;
  }
  .ap-counter p {
    margin: 0.25rem 0 0; color: var(--ap-dim);
    font-size: 0.76rem; line-height: 1.45;
  }

  /* The pipeline. Static markup, so it costs nothing to reconcile. */
  .ap-flow {
    display: flex; flex-wrap: wrap; gap: 0.35rem;
    align-items: center; margin: 0.5rem 0 0.2rem;
    padding: 0.9rem 1rem; border: 1px solid var(--ap-line);
    border-radius: 8px; background: var(--ap-surface);
  }
  .ap-node {
    border: 1px solid var(--ap-line-2); border-radius: 6px;
    padding: 0.42rem 0.7rem; font-size: 0.8rem; background: var(--ap-raised);
    white-space: nowrap; color: var(--ap-text);
  }
  .ap-node.is-gate {
    border-color: rgba(47,129,247,0.55); color: #CFE3FF;
    background: rgba(47,129,247,0.09);
  }
  .ap-arrow { color: var(--ap-dim); font-size: 0.85rem; }

  /* The mode strip: a badge and a sentence on one line, with a hairline
     under it. Loud enough to be read, quiet enough not to look like a fault. */
  .ap-mode {
    display: flex; gap: 0.7rem; align-items: baseline; flex-wrap: wrap;
    padding: 0.6rem 0 0.8rem; border-bottom: 1px solid var(--ap-line);
    color: var(--ap-muted); font-size: 0.85rem; margin-bottom: 0.3rem;
  }
  .ap-mode code {
    background: var(--ap-raised); border: 1px solid var(--ap-line);
    border-radius: 4px; padding: 0.05rem 0.35rem; font-size: 0.8rem;
    color: var(--ap-blue-2);
  }

  /* The evidence strip. Rules between rows rather than a box around each,
     so it reads as one statement instead of four competing ones. */
  .ap-guarantees {
    margin: 0.2rem 0 0.4rem; max-width: 76ch;
    border: 1px solid var(--ap-line); border-radius: 8px;
    background: var(--ap-surface); padding: 0.2rem 1.1rem;
  }
  .ap-guarantee {
    display: flex; gap: 1.2rem; align-items: baseline;
    padding: 0.7rem 0; border-top: 1px solid var(--ap-line);
    font-size: 0.86rem; color: var(--ap-muted);
  }
  .ap-guarantee:first-child { border-top: none; }
  .ap-guarantee-key {
    flex: 0 0 9rem; color: var(--ap-text); font-weight: 600;
    font-size: 0.78rem; letter-spacing: 0.02em;
  }

  .ap-pill {
    display: inline-block; font-size: 0.66rem; font-weight: 650;
    letter-spacing: 0.09em; padding: 0.2rem 0.5rem; border-radius: 4px;
    border: 1px solid currentColor;
  }

  /* Streamlit paints its warning pure yellow (hue 60) at 20% opacity, which
     composites to an olive block beside this project's gold amber (hue 40) --
     measured, not guessed: rgba(255,255,18,.2) against --ap-amber #D29922.
     Only the *presentation* changes here; every `st.warning` call keeps its
     meaning, and amber keeps meaning "stop and decide".

     `stAlertContainer` and `stAlertContentWarning` are Streamlit-internal test
     ids, so the rule is scoped as narrowly as the DOM allows and touches no
     other alert kind. If either name changes in an upgrade this stops applying
     and the default returns -- a silent fallback to a working control, which
     is the right way for a cosmetic rule to fail. */
  [data-testid="stAlertContainer"]:has([data-testid="stAlertContentWarning"]) {
    background: rgba(210, 153, 34, 0.13);
    border: 1px solid rgba(210, 153, 34, 0.42);
    color: #EBD9AE;
  }

  .ap-allow { color: var(--ap-green); }
  /* Not an outcome anyone should read as good or bad -- the platform simply
     had nothing to answer with. */
  .ap-neutral { color: var(--ap-muted); }
  .ap-deny  { color: var(--ap-red); }
  .ap-hold  { color: var(--ap-amber); }

  /* ============================================ Streamlit's own widgets
     The tokens in config.toml do the colours and radii. These are the few
     places where the default composition, not the default palette, is wrong. */

  /* Tabs read as a segmented control rather than as browser tabs. */
  [data-testid="stTabs"] [data-baseweb="tab-list"] {
    gap: 0.15rem; border-bottom: 1px solid var(--ap-line);
    background: transparent;
  }
  [data-testid="stTab"] {
    font-size: 0.85rem !important; font-weight: 500;
    color: var(--ap-muted); padding: 0.55rem 0.9rem !important;
  }
  [data-testid="stTab"][aria-selected="true"] { color: var(--ap-text); font-weight: 600; }

  [data-testid="stExpander"] details {
    border: 1px solid var(--ap-line); border-radius: 8px;
    background: var(--ap-surface);
  }
  [data-testid="stExpander"] summary { font-size: 0.86rem; font-weight: 600; }
  [data-testid="stExpander"] summary:hover { color: var(--ap-blue-2); }

  [data-testid="stDataFrame"] { border-radius: 8px; overflow: hidden; }

  /* A metric is a headline number; its label is the caption under a headline,
     not a heading of its own. */
  [data-testid="stMetricLabel"] {
    color: var(--ap-dim) !important;
    font-size: 0.76rem !important; letter-spacing: 0.03em;
  }
  /* The same surface the landing page's counter strip uses, so Empresa and
     Observabilidade read as the same product rather than as bare numbers on a
     background. One rule covers both, which is the point of putting it here
     instead of in each page. */
  [data-testid="stMetric"] {
    border: 1px solid var(--ap-line); background: var(--ap-surface);
    border-radius: 8px; padding: 0.85rem 1rem;
  }

  /* An example question is a thing you can run, not a code listing. Quieter
     than Streamlit's default block, and the same surface as everything else. */
  [data-testid="stCode"] pre {
    background: var(--ap-surface) !important;
    border: 1px solid var(--ap-line); border-radius: 6px;
  }
  [data-testid="stCode"] code { font-size: 0.84rem; color: #CFE3FF; }

  [data-testid="stTextInputRootElement"] { background: var(--ap-surface); }
  [data-testid="stTextInputField"] { font-size: 0.92rem; }

  [data-testid="stButton"] button { font-size: 0.87rem; font-weight: 600; }
  [data-testid="stBaseButton-primary"] {
    box-shadow: 0 1px 0 rgba(255,255,255,0.06) inset,
                0 6px 18px -8px rgba(47,129,247,0.6);
  }
  [data-testid="stBaseButton-secondary"] { background: var(--ap-surface); }
  [data-testid="stBaseButton-secondary"]:hover { border-color: var(--ap-line-2); }

  /* The scene's iframe is a surface like any other, and says so. The border
     lives out here rather than inside the iframe: an inner border would sit a
     scrollbar's width away from the edge whenever the block scrolls. */
  [data-testid="stIFrame"] {
    border-radius: 8px; border: 1px solid var(--ap-line);
    background: var(--ap-surface);
  }

  /* Streamlit's top bar is empty in viewer mode and only steals height. */
  [data-testid="stHeader"] { background: transparent; height: 2.4rem; }

  /* Streamlit hangs an anchor-link icon off every heading it renders, and a
     copy button off every code block and dataframe. `anchor=False` removes the
     icon at the source wherever the API takes it -- the node never reaches the
     page. Two cases have no such parameter: headings written as raw HTML
     inside `st.markdown`, and the element toolbars (copy, search, download,
     fullscreen). Those go from here. `display: none` rather than a visual
     hide, so they leave the tab order and the accessibility tree too. */
  [data-testid="stHeaderActionElements"],
  [data-testid="stElementToolbar"],
  [data-testid="stElementToolbarButton"] { display: none !important; }

  /* ===================================================== the sidebar */

  /* Streamlit reserves 60px at the top of the sidebar for a logo slot this app
     never fills -- `st.logo` is not called, so `stLogoSpacer` is 0px wide and
     32px tall -- plus the collapse chevron. Together with the gap below it the
     brand lockup started 86px down an otherwise empty panel. The empty slot
     goes, and the header leaves the flow so the chevron keeps its corner and
     stays clickable while the content starts at the top. */
  [data-testid="stSidebarContent"] { position: relative; }
  [data-testid="stSidebarHeader"] {
    position: absolute; top: 0.35rem; right: 0.6rem; left: auto;
    width: auto; height: auto; padding: 0; z-index: 2;
  }
  [data-testid="stLogoSpacer"] { display: none; }
  [data-testid="stSidebarUserContent"] { padding-top: 0.85rem; }

  /* The chevron now shares the brand's row, so the two are kept apart by
     geometry rather than by hoping the name stays short. Only that row: the
     chevron ends at y=34 and the category starts at 42, so padding there would
     only cost the category width it needs at the narrow breakpoint. */
  .ap-brand { padding-right: 2.2rem; }

  /* Brand lockup: a mark, a name, a category. The mark is drawn in CSS -- a
     bitmap would be one more asset to load and one more request to justify. */
  .ap-brand { display: flex; gap: 0.6rem; align-items: center; margin-bottom: 0.15rem; }
  .ap-brandmark {
    width: 26px; height: 26px; border-radius: 7px; flex: 0 0 26px;
    display: flex; align-items: center; justify-content: center;
    font-size: 0.85rem; font-weight: 700; color: #fff;
    background: linear-gradient(150deg, var(--ap-blue-2), var(--ap-blue) 62%);
    box-shadow: 0 3px 12px -4px rgba(47,129,247,0.75);
  }
  .ap-brandname {
    font-size: 1.06rem; font-weight: 650; letter-spacing: -0.02em;
    color: var(--ap-text); line-height: 1.1;
  }
  .ap-brandcat {
    font-size: 0.66rem; letter-spacing: 0.1em; text-transform: uppercase;
    color: var(--ap-dim); margin: 0.1rem 0 0.9rem;
  }

  /* Status. A dot and two words -- the pattern every operations console uses,
     because it is readable at a glance and takes one line. */
  .ap-status {
    display: inline-flex; align-items: center; gap: 0.45rem;
    border: 1px solid var(--ap-line); border-radius: 999px;
    padding: 0.25rem 0.65rem; font-size: 0.72rem; color: var(--ap-muted);
    background: var(--ap-surface);
  }
  .ap-status i {
    width: 6px; height: 6px; border-radius: 50%; background: var(--ap-green);
    box-shadow: 0 0 0 3px rgba(63,185,80,0.16);
  }

  .ap-navlabel {
    font-size: 0.64rem; letter-spacing: 0.14em; text-transform: uppercase;
    color: var(--ap-dim); margin: 1.3rem 0 0.4rem; font-weight: 600;
  }
  /* The sentence under the list explains the shared marker. Uppercase and
     letterspaced it ran to three lines and read as a second section heading,
     which is not what a footnote is. */
  .ap-navnote {
    font-size: 0.72rem; color: var(--ap-dim); margin: 0.55rem 0 0;
    line-height: 1.45;
  }

  /* Environment: the one card in the sidebar, because "which mode am I in"
     and "whose data is this" are the two questions a stranger asks first. */
  .ap-env {
    border: 1px solid var(--ap-line); border-radius: 8px;
    background: var(--ap-surface); padding: 0.7rem 0.8rem; margin-top: 0.3rem;
  }
  .ap-env-row { display: flex; align-items: center; gap: 0.5rem; }
  .ap-env p {
    margin: 0.5rem 0 0; font-size: 0.72rem; line-height: 1.5;
    color: var(--ap-dim);
  }
  .ap-env code {
    display: block; margin-top: 0.5rem; font-size: 0.7rem;
    color: var(--ap-muted); background: var(--ap-ground);
    border: 1px solid var(--ap-line); border-radius: 4px; padding: 0.25rem 0.4rem;
    overflow-wrap: anywhere;
  }

  /* Navigation. `st-key-nav_pt` / `st-key-nav_en` is the widget's own key,
     stamped on its container by Streamlit -- a hook this project owns, unlike
     the emotion hash beside it, which changes on upgrade. */
  [class*="st-key-nav_"] [data-testid="stRadioGroup"] { gap: 0.1rem; }
  [class*="st-key-nav_"] [data-testid="stRadioOption"] {
    padding: 0.4rem 0.55rem; border-radius: 6px; margin: 0;
    border: 1px solid transparent;
    transition: background 0.15s ease, border-color 0.15s ease;
  }
  [class*="st-key-nav_"] [data-testid="stRadioOption"]:hover {
    background: var(--ap-surface);
  }
  /* The radio dot. Its own element carries only emotion hashes, so the rule
     walks the structure instead: label > wrapper > row > [dot, label]. If that
     nesting ever changes the dot simply comes back, which is the right way for
     a cosmetic rule to fail. The input itself is untouched and still focusable
     -- the row shows selection through `data-selected`, which Streamlit sets. */
  [class*="st-key-nav_"] [data-testid="stRadioOption"] > div > div > div:first-child {
    display: none;
  }
  [class*="st-key-nav_"] [data-testid="stRadioOption"] p {
    font-size: 0.85rem !important; color: var(--ap-muted); font-weight: 500;
  }
  [class*="st-key-nav_"] [data-testid="stRadioOption"][data-selected="true"] {
    background: var(--ap-raised); border-color: var(--ap-line-2);
  }
  [class*="st-key-nav_"] [data-testid="stRadioOption"][data-selected="true"] p {
    color: var(--ap-text); font-weight: 600;
  }
  /* A hairline of accent on the active row -- the one place the sidebar uses
     the primary colour, so it always means "you are here". */
  [class*="st-key-nav_"] [data-testid="stRadioOption"][data-selected="true"]::before {
    content: ""; position: absolute; left: 0; top: 20%; bottom: 20%;
    width: 2px; border-radius: 2px; background: var(--ap-blue);
  }
  [class*="st-key-nav_"] [data-testid="stRadioOption"] { position: relative; }

  /* Language. Closed it is one row -- flag, language, chevron; open it is the
     same row twice with the one in use marked. Every hook here is a `st-key-`
     class this project owns, for the reason the navigation above it gives.
     The chevron is Streamlit's own `stIconMaterial`, restyled rather than
     replaced. The flags are drawn: see `_FLAG_SVG`. */
  .ap-langlabel {
    font-size: 0.64rem; letter-spacing: 0.14em; text-transform: uppercase;
    color: var(--ap-dim); font-weight: 600; margin: 0 0 0.3rem;
  }

  /* The flag, in the same place on the closed control and on each option. */
  [class*="st-key-lang_open_"] [data-testid="stPopoverButton"] p::before,
  [class*="st-key-lang_opt_"] button p::before {
    content: ""; flex: 0 0 18px; height: 13px; margin-right: 0.55rem;
    border-radius: 2px; background-size: cover; background-position: center;
    box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.16);
  }
  [class*="st-key-lang_open_"] [data-testid="stPopoverButton"] p,
  [class*="st-key-lang_opt_"] button p {
    display: flex; align-items: center; width: 100%; margin: 0;
    font-size: 0.82rem !important; line-height: 1.2;
  }
  [class*="st-key-lang_open_pt"] [data-testid="stPopoverButton"] p::before,
  [class*="st-key-lang_opt_pt"] button p::before {
    /* Brazil, on an 18x13 viewBox -- the size it is drawn at -- so every
       coordinate is a whole unit and nothing has to be scaled. */
    /* The backslashes are Python line continuations: `_STYLE` is an
       ordinary string, so it holds this URL as one unbroken token and
       the CSS never sees a line break. */
    background-image: url("data:image/svg+xml,\
<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 18 13'>\
<rect width='18' height='13' fill='%23009B3A'/>\
<path d='M9 1.4 16.6 6.5 9 11.6 1.4 6.5Z' fill='%23FEDF00'/>\
<circle cx='9' cy='6.5' r='2.9' fill='%23002776'/></svg>");
  }
  [class*="st-key-lang_open_en"] [data-testid="stPopoverButton"] p::before,
  [class*="st-key-lang_opt_en"] button p::before {
    /* The United States: thirteen bands on a 13-unit box, so the seven
       red ones fall on the even rows, and a canton over the top seven. */
    /* The backslashes are Python line continuations: `_STYLE` is an
       ordinary string, so it holds this URL as one unbroken token and
       the CSS never sees a line break. */
    background-image: url("data:image/svg+xml,\
<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 18 13'>\
<rect width='18' height='13' fill='%23F7F9FC'/>\
<rect y='0' width='18' height='1' fill='%23B22234'/>\
<rect y='2' width='18' height='1' fill='%23B22234'/>\
<rect y='4' width='18' height='1' fill='%23B22234'/>\
<rect y='6' width='18' height='1' fill='%23B22234'/>\
<rect y='8' width='18' height='1' fill='%23B22234'/>\
<rect y='10' width='18' height='1' fill='%23B22234'/>\
<rect y='12' width='18' height='1' fill='%23B22234'/>\
<rect width='7.2' height='7' fill='%233C3B6E'/></svg>");
  }

  /* Closed. */
  [class*="st-key-lang_open_"] [data-testid="stPopoverButton"] {
    width: 100%; justify-content: space-between; gap: 0.4rem;
    background: var(--ap-surface); border: 1px solid var(--ap-line);
    border-radius: 8px; padding: 0.42rem 0.5rem 0.42rem 0.6rem;
    min-height: 0; height: auto;
    transition: border-color 0.15s ease, background 0.15s ease;
  }
  [class*="st-key-lang_open_"] [data-testid="stPopoverButton"]:hover {
    background: var(--ap-raised); border-color: var(--ap-line-2);
  }
  [class*="st-key-lang_open_"]
    [data-testid="stPopoverButton"][aria-expanded="true"] {
    background: var(--ap-raised); border-color: var(--ap-blue);
  }
  [class*="st-key-lang_open_"] [data-testid="stPopoverButton"] p {
    color: var(--ap-text); font-weight: 500;
  }
  [class*="st-key-lang_open_"] [data-testid="stIconMaterial"] {
    color: var(--ap-dim); font-size: 1.1rem;
  }

  /* Open. The panel is the width of the control, not of the page, and it
     cannot grow past the viewport at the narrow end. */
  [data-testid="stPopoverBody"]:has([class*="st-key-lang_opt_"]) {
    /* Streamlit puts `min-width: 16.2rem` on this panel, which beat a
       plain `width` and left it 259px wide inside a 173px control. The
       panel is `position: fixed` in a portal, so it cannot read the width
       of the control it drops from, and hard-coding that width would mean
       tracking Streamlit's sidebar padding at every breakpoint -- measured,
       it is 20px at 768 and 10px at 480. It sizes to its options instead,
       with a floor so two short words still read as a menu. */
    width: max-content; min-width: 11.5rem;
    max-width: calc(100vw - 2.5rem);
    padding: 0.28rem; background: var(--ap-raised);
    border: 1px solid var(--ap-line-2); border-radius: 8px;
    box-shadow: 0 10px 28px rgba(0, 0, 0, 0.45);
  }
  [data-testid="stPopoverBody"]:has([class*="st-key-lang_opt_"])
    [data-testid="stVerticalBlock"] { gap: 0.12rem; }

  /* One option. */
  [class*="st-key-lang_opt_"] button {
    width: 100%; justify-content: flex-start;
    background: transparent; border: 1px solid transparent;
    border-radius: 6px; padding: 0.4rem 0.55rem; min-height: 0; height: auto;
    transition: background 0.14s ease, border-color 0.14s ease;
  }
  [class*="st-key-lang_opt_"] button:hover {
    background: var(--ap-surface); border-color: var(--ap-line);
  }
  [class*="st-key-lang_opt_"] button p { color: var(--ap-muted); }
  [class*="st-key-lang_opt_"] button[kind="primary"] p { color: var(--ap-blue-2); }
  [class*="st-key-lang_opt_"] button[kind="primary"] p::after {
    /* The glyph itself rather than a CSS hex escape: `_STYLE` is an
       ordinary Python string, so a backslash-escape for this codepoint is
       read as octal there and reached the page as two wrong characters. */
    content: "✓"; margin-left: auto; padding-left: 0.7rem;
    color: var(--ap-blue); font-weight: 700; font-size: 0.9em;
  }

  /* ===================================================== narrow screens */
  @media (max-width: 900px) {
    .block-container { padding-left: 1rem; padding-right: 1rem; }
    [data-testid="stMain"] h2 { font-size: 0.96rem !important; margin-top: 1.9rem; }
    .ap-lede { font-size: 0.94rem; }
    /* 0.7rem is 11px, which is small for uppercase on a phone. */
    .ap-eyebrow { font-size: 0.76rem; }
    .ap-card p { font-size: 0.88rem; }
    .ap-node { font-size: 0.76rem; padding: 0.32rem 0.55rem; }
    .ap-counter { flex: 1 1 100%; border-left: none; border-top: 1px solid var(--ap-line); }
    .ap-counter:first-child { border-top: none; }
    .ap-guarantee { flex-direction: column; gap: 0.2rem; }
    .ap-guarantee-key { flex: none; }

    section[data-testid="stSidebar"] .block-container {
      padding-left: 0.9rem; padding-right: 0.9rem;
    }
  }

  /* Streamlit keeps the sidebar as a fixed 300px panel until its own much
     narrower breakpoint, which is 39% of a 768px tablet -- the navigation
     taking more room than the thing being navigated. Narrowing it gives the
     content back the majority of the screen.

     Bounded below at 640px, and the lower bound is the point. The rule used to
     live in the `max-width: 900px` block with no floor, so a phone got the same
     214px: 57% of a 375px screen, against the 28% it was reasoned about for a
     tablet. Measured on a 375x812 viewport the panel overlapped a full-width
     main and cut the hero mid-word. Below 640px Streamlit's own collapsed
     behaviour is the better answer and this rule stays out of its way.

     Expanded only. Collapsed, Streamlit shrinks the panel with `max-width: 0`,
     and an unconditional `min-width: 214px` beat it: a closed sidebar still
     reserved a 214px empty strip, which squeezed the cards on a 768px tablet
     to 113px and broke words mid-syllable. */
  @media (min-width: 640px) and (max-width: 900px) {
    section[data-testid="stSidebar"][aria-expanded="true"] {
      width: 214px !important; min-width: 214px !important;
    }
  }

  /* ============================================ touch and narrow screens */
  /* 16px, and not a pixel less, on anything focusable that takes text. Safari
     on iOS and iPadOS zooms the page when a focused input's font is under 16px
     and does not zoom back out when the field is left, so the visitor is
     stranded at 1.4x on the one control this whole page exists for. This is
     the reason for the number; it is not a typography choice. Tablets too:
     the old phone-only rule left a 768px iPad at 14.7px. */
  @media (max-width: 1024px), (pointer: coarse) {
    [data-testid="stTextInputField"],
    [data-testid="stTextInputField"] input,
    [data-testid="stTextArea"] textarea,
    [data-testid="stNumberInputField"] { font-size: 16px !important; }
  }
</style>
"""


@st.cache_resource
def get_platform() -> AgentPlatform:
    """One platform instance per Streamlit session.

    A key that is configured but not authorised is the one startup failure this
    app can answer for itself. ``build_provider`` refuses it on purpose --
    holding a key is not permission to spend it -- and that refusal is correct
    and stays exactly as it is. What was wrong was the dashboard's answer to
    it: none. It failed to start and showed a bare ``LiveNotAuthorised`` to the
    visitor while a complete, working demo mode sat one branch away.

    So the *interface* degrades rather than the gate. The settings are rebuilt
    without the key, which is byte for byte the state of having no key at all,
    and the sidebar's existing DEMO / STUB badge then says so in its own words.

    Only this one exception is caught, and only around construction. A Gemini
    failure, a gateway error, an unreadable database -- those are real faults
    and must still stop the app rather than be quietly relabelled as a demo.
    """
    settings = Settings.from_env()
    try:
        return AgentPlatform(settings)
    except LiveNotAuthorised:
        return AgentPlatform(replace(settings, gemini_api_key=None))


#: How many of the declared capabilities the landing page shows. The
#: architecture page renders the rest, so the split lives in one place and the
#: two pages cannot both claim -- or both drop -- the same card.
#: Re-exported so `page_architecture` and `test_dashboard_rendering` keep
#: reading one value. It is declared beside the page that shows the first
#: slice, because that page is what decides how many there are.
OVERVIEW_CAPABILITIES = overview_page.OVERVIEW_CAPABILITIES

#: Stands for "the usual hint": the sentence itself is looked up when the page
#: is drawn, so it is in the visitor's language rather than the one this module
#: happened to be imported under.
DEFAULT_HINT: Final[object] = object()


def no_data(message: str, *, hint: str | object | None = DEFAULT_HINT) -> None:
    """Empty state carrying the command that actually populates *this* page."""
    if hint is DEFAULT_HINT:
        hint = t("hint.populate")
    st.info(message if hint is None else f"{message}\n\n{hint}")


def state_label(state: execution_view.State) -> str:
    """A run state as the visitor's language says it.

    The enum's values are identifiers, one of them Portuguese and the rest
    English, and both pages showed them raw: "SEM RESPOSTA" on the English page,
    "SUCCESS" on the Portuguese one.
    """
    return t(f"state.{state.name.lower()}")


def _localised_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Column headings and vocabulary in the visitor's language.

    The rows keep their English keys and stored values -- they are the same
    records the agents read -- and only what is drawn is translated. A value
    with no entry (a name, an id, a date) is shown as it is.
    """
    def value(cell: Any) -> Any:
        if isinstance(cell, str):
            key = f"val.{cell}"
            text = t(key)
            return cell if text == key else text
        return cell

    return [{t(f"col.{k}"): value(v) for k, v in row.items()} for row in rows]


def eyebrow(text: str) -> None:
    st.markdown(f'<p class="ap-eyebrow">{text}</p>', unsafe_allow_html=True)


def lede(text: str) -> None:
    """A muted sub-heading paragraph.

    This is raw HTML, so markdown is not parsed inside it: emphasis has to be
    written as `<strong>`, not as `**bold**`, which renders as four literal
    asterisks. Same for `eyebrow`.
    """
    st.markdown(f'<p class="ap-lede">{text}</p>', unsafe_allow_html=True)


def cards(items: list[tuple[str, str]], per_row: int = 3) -> None:
    """A row of equal cards. Fixed count per row, so the layout never reflows
    into a different number of elements between reruns."""
    for start in range(0, len(items), per_row):
        row = items[start : start + per_row]
        columns = st.columns(per_row)
        for column, (title, body) in zip(columns, row, strict=False):
            column.markdown(
                f'<div class="ap-card ap-fade"><h4>{title}</h4><p>{body}</p></div>',
                unsafe_allow_html=True,
            )


def flow(nodes: list[str], gate: str | None = None) -> None:
    """The request pipeline, as static markup."""
    parts = []
    for index, node in enumerate(nodes):
        css = "ap-node is-gate" if node == gate else "ap-node"
        parts.append(f'<span class="{css}">{node}</span>')
        if index < len(nodes) - 1:
            parts.append('<span class="ap-arrow">&rarr;</span>')
    st.markdown(f'<div class="ap-flow">{"".join(parts)}</div>', unsafe_allow_html=True)


def as_prose(text: str) -> str:
    """Prepare platform text for `st.markdown` without letting it reinterpret it.

    Streamlit reads `$...$` as inline LaTeX. Brazilian currency is written
    `R$`, so a sentence with two amounts in it -- which every order summary and
    every revenue total has -- pairs the dollar signs, swallows them, and
    renders the text between as maths. Measured on the real page:

        "Os 40 pedidos somam R 33.002,50. Descontando (R 6.374,90),
         o valor efetivo é R$ 26.627,60."

    Three amounts, two spellings, one sentence. Escaping at the boundary fixes
    every caller at once: the tools keep writing `R$`, and the CLI and the API
    are unaffected because they never went through a markdown renderer.
    """
    return text.replace("$", chr(92) + "$")


def goto(page: str, question: str | None = None) -> None:
    """Queue a navigation for the next rerun. *page* is a slug, not a label.

    Writes state and returns. It deliberately does not call ``st.rerun()``:
    a rerun raised from inside a tab or an expander tears down a subtree that
    React is still holding a reference into, which is what produced
    `insertBefore` errors in the previous version. Streamlit reruns on its own
    after a button press, and the radio picks the queued page up then.
    """
    st.session_state["nav"] = page
    if question is not None:
        st.session_state["queued_question"] = question


# ============================================================ 1. Visão geral


def page_overview(platform: AgentPlatform) -> None:
    """The landing page, which lives in `overview.py`.

    Delegated rather than written here so the rebuild could not reach the other
    five pages: everything it draws is namespaced `ov-` and defined in a module
    nothing else imports. `goto` is passed in because that module cannot import
    this one back.
    """
    overview_page.render(platform, goto=goto)


# ================================================================ 2. Empresa


def _table(rows: list[dict[str, Any]], preview: int, key: str) -> None:
    """Show a readable slice, with the rest one disclosure away."""
    if not rows:
        no_data(t("table.no_rows"), hint=None)
        return
    rows = _localised_rows(rows)
    st.dataframe(rows[:preview], width="stretch", hide_index=True)
    if len(rows) > preview:
        with st.expander(t("table.see_all", n=len(rows))):
            st.dataframe(rows, width="stretch", hide_index=True, key=f"all_{key}")


def page_company(platform: AgentPlatform) -> None:
    eyebrow(t("company.eyebrow"))
    st.title(demo.COMPANY_NAME, anchor=False)
    lede(
        t("company.lede")
    )

    # Before the numbers, because a visitor who reads the numbers first has
    # already started believing them. `st.info` and not a caption: this is the
    # single most important sentence on the page.
    st.info(
        f"**{t('company.fictional_title')}** — {t('company.fictional_body')}",
        icon=":material/science:",
    )

    totals = demo.company_totals()
    st.write("")
    metrics = st.columns(4)
    metrics[0].metric(t("company.customers"), totals["Customers"])
    metrics[1].metric(t("company.orders"), totals["Orders"])
    metrics[2].metric(t("company.products"), totals["Products"])
    metrics[3].metric(t("company.open_tickets"), totals["Open tickets"])

    st.header(t("company.the_data"), anchor=False)
    # Six families, which is what the dataset holds. Shipments and the
    # knowledge base were in it all along and had no tab, so the explorer
    # showed a visitor less than the agents can actually reach. The labels come
    # from the catalogue -- they were Portuguese literals, and stayed
    # Portuguese with the interface in English.
    customers, orders, products, tickets, shipments, articles = st.tabs(
        [
            t("company.tab_customers"),
            t("company.tab_orders"),
            t("company.tab_products"),
            t("company.tab_tickets"),
            t("company.tab_shipments"),
            t("company.tab_kb"),
        ]
    )
    with customers:
        _table(demo.customers_table(), 6, "customers")
    with orders:
        _table(demo.orders_table(), 6, "orders")
    with products:
        _table(demo.products_table(), 6, "products")
    with tickets:
        _table(demo.tickets_table(), 6, "tickets")
    with shipments:
        _table(demo.shipments_table(), 6, "shipments")
    with articles:
        _table(demo.kb_table(), 6, "kb")

    st.header(t("company.what_you_can_test"), anchor=False)
    lede(
        t("company.ids_are_real")
    )
    for index, (question, hint) in enumerate(demo.READ_ONLY_EXAMPLES[:3]):
        columns = st.columns([6, 2])
        columns[0].code(question, language="text", wrap_lines=True)
        columns[0].caption(hint)
        columns[1].button(
            t("company.ask"),
            key=f"company_ask_{index}",
            width="stretch",
            on_click=goto,
            args=("orchestrator", question),
        )


# =========================================================== 3. Orquestrador


def _decision_pill(decision: str | None) -> str:
    text = (decision or "—").upper()
    css = {"ALLOW": "ap-allow", "DENY": "ap-deny", "REQUIRE_CONFIRMATION": "ap-hold"}.get(
        text, "ap-hold"
    )
    return f'<span class="ap-pill {css}">{text}</span>'


def _outcome_pill(state: execution_view.State) -> str:
    css = {
        execution_view.State.SUCCESS: "ap-allow",
        execution_view.State.BLOCKED: "ap-deny",
        execution_view.State.FAILED: "ap-deny",
        # Neutral on purpose. Nothing refused anything, so the badge must not
        # borrow the colour of a denial.
        execution_view.State.OUT_OF_SCOPE: "ap-neutral",
    }.get(state, "ap-hold")
    return f'<span class="ap-pill {css}">{state_label(state)}</span>'


def _result_summary(last: dict[str, Any], view: execution_view.ExecutionView) -> None:
    """Four facts, in the order a reader asks for them."""
    st.markdown(t("result.question_label"))
    st.code(last["question"], language="text", wrap_lines=True)

    columns = st.columns(4)
    with columns[0]:
        st.caption(t("result.decision"))
        st.markdown(_decision_pill(view.policy_decision), unsafe_allow_html=True)
    with columns[1]:
        st.caption(t("result.agent"))
        st.markdown(f"`{last.get('route') or '—'}`")
    with columns[2]:
        st.caption(t("result.tool"))
        # The tool the decision beside it was about. `view.tools[0]` was the
        # first tool in the trace, which on a multi-step run is not the one the
        # policy decided on -- a confirmation showed `search` next to
        # REQUIRE_CONFIRMATION while asking to approve `update_record`.
        # Falls back to the last tool that ran when no policy event named one,
        # so a trace with any number of tools still reports something true.
        tool = view.decided_tool or (view.tools[-1].name if view.tools else "—")
        st.markdown(f"`{tool}`")
    with columns[3]:
        st.caption(t("result.time"))
        st.markdown(f"**{last['latency_ms'] / 1000:.2f}s**")


def _timeline(view: execution_view.ExecutionView) -> None:
    """The essential shape of the run. Six-ish lines, not twenty."""
    # Plain characters rather than `:material/...:` shortcodes: st.markdown
    # renders those literally, which put the words "radio_button_unchecked"
    # into the timeline. A glyph that renders everywhere beats one that renders
    # in some widgets and leaks its own name in others.
    marks = {
        execution_view.State.SUCCESS: ("●", "ap-allow"),
        execution_view.State.BLOCKED: ("■", "ap-deny"),
        execution_view.State.FAILED: ("■", "ap-deny"),
        execution_view.State.WAITING: ("◐", "ap-hold"),
        execution_view.State.OUT_OF_SCOPE: ("○", "ap-neutral"),
    }
    for step in view.steps:
        glyph, css = marks.get(step.state, ("○", ""))
        st.markdown(
            f'<div class="ap-fade"><span class="{css}">{glyph}</span> '
            f"{step.label}</div>",
            unsafe_allow_html=True,
        )
        if step.detail:
            # An ideographic space indents the detail under its step, which a
            # normal space would not survive: markdown collapses leading runs
            # of ASCII whitespace.
            st.caption(f"　{step.detail}")


def _technical_details(last: dict[str, Any], view: execution_view.ExecutionView) -> None:
    with st.expander(t("tech.see_details")):
        st.caption(t("tech.correlation_id"))
        st.code(last["request_id"], language="text", wrap_lines=True)

        st.caption(t("tech.policy_decision"))
        st.markdown(
            f"{_decision_pill(view.policy_decision)} &nbsp; "
            f"{t('tech.rules')}: `{view.policy_rules or '—'}`",
            unsafe_allow_html=True,
        )

        if view.tools:
            st.caption(t("tech.tools"))
            st.dataframe(
                [
                    {
                        t("tools.name"): tool.name,
                        t("tools.risk"): tool.risk or "—",
                        t("tools.proposed_by"): tool.proposed_by or "—",
                        t("tools.decision"): tool.decision or "—",
                        t("tools.execution"): state_label(tool.execution),
                    }
                    for tool in view.tools
                ],
                width="stretch",
                hide_index=True,
            )

        st.caption(t("tech.agents"))
        st.dataframe(
            [
                {
                    t("tech.col_agent"): a.name,
                    t("tech.col_state"): state_label(a.state),
                    t("tech.col_detail"): a.detail or "—",
                }
                for a in view.agents
            ],
            width="stretch",
            hide_index=True,
        )

        st.caption(t("tech.full_event_sequence"))
        st.dataframe(
            [
                {
                    "#": event.get("sequence"),
                    t("tech.col_event"): event.get("event_type"),
                    t("tech.col_agent"): event.get("agent") or "—",
                    t("tech.col_tool"): event.get("tool") or "—",
                    t("tech.col_status"): event.get("status"),
                    "ms": event.get("latency_ms"),
                }
                for event in last["events"]
            ],
            width="stretch",
            hide_index=True,
        )
        st.caption(
            t("result.model_calls", n=execution_view.model_calls(last["events"]))
        )


def _visitor_dataset() -> WorkingSet:
    """This browser session's private copy of the simulated HDstore records.

    Held in session state, so it lives exactly as long as the visitor's session
    and is never seen by another one. Created on first use, from the pristine
    dataset, which is also what a new visitor starts from.
    """
    working_set = st.session_state.get("_dataset")
    if not isinstance(working_set, WorkingSet):
        working_set = WorkingSet()
        st.session_state["_dataset"] = working_set
    return working_set


def _resolve(platform: AgentPlatform, approved: bool) -> None:
    """Approve or decline the suspended action, then let Streamlit rerun.

    A callback rather than a body-level `st.rerun()`: the callback runs before
    the rerun Streamlit already schedules for a button press, so the state is
    current by the time the page redraws and no subtree is torn down mid-render.
    """
    last = st.session_state.get("last_result")
    if not last or not last.get("pending"):
        return
    with use_locale(current_locale()), dataset_scope(_visitor_dataset()):
        resumed = platform.confirm(
            last["request_id"], approved=approved, actor="dashboard-user", source="ui"
        )
    st.session_state["last_result"] = {
        **last,
        "status": resumed.status,
        "response": resumed.response,
        "pending": None,
        "events": platform.repository.events_for_request(last["request_id"]),
    }


def _confirmation_panel(platform: AgentPlatform, last: dict[str, Any]) -> None:
    """The human-in-the-loop control, not a description of one."""
    pending = last["pending"]
    st.warning(
        t("confirm.needs_human", tool=pending["tool"], risk=pending["risk_level"])
    )
    st.caption(
        t("confirm.bound_to_args")
    )
    st.json(pending.get("arguments") or {})
    decide = st.columns([1, 1, 4])
    decide[0].button(
        t("confirm.approve"), key="confirm_approve", type="primary", width="stretch",
        on_click=_resolve, args=(platform, True),
    )
    decide[1].button(
        t("confirm.decline"), key="confirm_decline", width="stretch",
        on_click=_resolve, args=(platform, False),
    )


#: Agent identifier -> catalogue key. Identifiers are the platform's, stable
#: and English; only the value a reader sees is translated. An agent absent
#: from this map simply does not announce itself.
_PROGRESS_STEPS: dict[str, str] = {
    "router": "progress.router",
    "researcher": "progress.researcher",
    "executor": "progress.executor",
    "validator": "progress.validator",
    "answerer": "progress.finishing",
}

#: Events that mean the work is over, whichever way it ended.
_PROGRESS_FINAL = frozenset(
    {"request_completed", "request_failed", "confirmation_requested"}
)


#: The one header trusted to name the visitor.
#:
#: **This is a statement about the deployment, not about proxies in general.**
#: The dashboard is deployed on Railway, whose edge terminates the connection
#: and writes the remote client address into ``X-Real-IP``. A single header
#: written by a single known proxy is something a browser cannot author,
#: because the edge overwrites whatever arrived.
#:
#: ``X-Forwarded-For`` is deliberately **not** read, not even as a fallback. It
#: is a chain whose length and direction depend on how many proxies sit in
#: front, and reading it correctly means knowing that number: too few hops
#: over-groups visitors, too many starts reading entries the browser supplied.
#: With one named platform there is no reason to take on a control whose
#: correctness depends on a count nobody re-checks.
#:
#: **If this app is ever put behind a different proxy, this trust boundary has
#: to be revisited.** A proxy that does not write ``X-Real-IP`` leaves every
#: visitor in the degraded fallback below; a proxy that forwards a client's
#: ``X-Real-IP`` unchanged would let a visitor name their own bucket. Neither is
#: detectable from inside this process, so the check belongs with the
#: deployment decision rather than in a generic abstraction here.
_CLIENT_IP_HEADER: Final[str] = "x-real-ip"

#: Salt for the visitor hash.
#:
#: Without one, a hash of an IPv4 address is not an anonymisation: the whole
#: space is four billion entries and a table of them is minutes of work. With
#: one, a quota key that leaks into a log or a metric reveals nothing.
#:
#: An unset salt falls back to a value generated for this process rather than
#: to a constant. That keeps the anonymisation real; the cost is that a restart
#: starts everybody's quota window over, which is why deployments should set it.
_VISITOR_SALT: Final[str] = (
    os.getenv("VISITOR_ID_SALT", "").strip() or secrets.token_hex(16)
)


def _canonical_ip(raw: str | None) -> str | None:
    """One address, written one way, or ``None``.

    The bucket is a hash of this string, so two spellings of one host are two
    buckets and the limit silently halves for that visitor. That is not
    hypothetical: an address used verbatim made ``203.0.113.9`` and
    ``203.0.113.9:44321`` different visitors, and one IPv6 host in four legal
    spellings four visitors. ``ipaddress`` decides what an address *is*; this
    function only decides what is worth handing it.

    Parsing is strict on purpose. Anything ambiguous returns ``None`` and the
    caller falls back, because an ambiguous value that is coerced into an
    identity is an identity somebody else can collide with or choose.
    """
    if not raw:
        return None
    text = raw.strip()
    # One address, not a list. A comma means somebody sent a forwarding chain
    # where a single address belongs, and picking an element of it here would
    # be exactly the guess this implementation exists to avoid.
    if not text or "," in text or any(char.isspace() for char in text):
        return None

    # A port, handled explicitly rather than tolerated. Railway sends a bare
    # address; these two forms are accepted because the alternative -- refusing
    # them -- would drop every visitor into the weaker session bucket if the
    # platform ever started including one, which is the same silent degradation
    # this function was written to end. Any *other* shape still fails below.
    if text.startswith("[") and "]" in text:  # [2001:db8::1] or [2001:db8::1]:443
        host, _, port = text.partition("]")
        if port not in ("", ":") and port[:1] == ":" and not port[1:].isdigit():
            return None
        text = host[1:]
    elif text.count(":") == 1 and "." in text:  # 203.0.113.9:44321
        host, _, port = text.partition(":")
        if not port.isdigit():
            return None
        text = host

    try:
        address = ip_address(text)
    except ValueError:
        # Hostnames, empty strings, prose, truncated addresses, CIDR blocks.
        return None

    # An IPv4 host reached over a v4-mapped v6 socket is the same host. Folding
    # it to the v4 form keeps that one bucket instead of two.
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        return str(mapped)
    # `compressed` is what makes 2001:db8:0:0:0:0:0:1 and 2001:DB8::1 agree.
    return address.compressed


def _client_address(
    headers: dict[str, str] | None, fallback_ip: str | None
) -> str | None:
    """The address Railway's edge reported, canonicalised, or ``None``.

    Header lookup is case-insensitive because the case a proxy uses is not a
    promise. Only ``X-Real-IP`` is consulted -- see ``_CLIENT_IP_HEADER`` for
    why that is a statement about this deployment and not a general rule.

    Nothing the page can set is read. ``X-Visitor-IP``, ``X-Client-IP``,
    ``X-Forwarded-For`` and ``Forwarded`` are all headers a browser or an
    intermediate can author, and any of them naming the bucket would mean a
    visitor could ask for a fresh allowance on every request.
    """
    for name, value in (headers or {}).items():
        if name.lower() == _CLIENT_IP_HEADER:
            canonical = _canonical_ip(value)
            if canonical is not None:
                return canonical
            # The header was present and unusable. Fall through rather than
            # returning None outright: the socket peer is still worth trying,
            # and it is subject to the same parsing.
            break

    # No usable header. `st.context.ip_address` is the socket peer, which
    # Streamlit documents as unsuitable for security decisions -- behind
    # Railway's edge it is the edge, so it groups every visitor together. It is
    # a fallback for running unproxied (a laptop, a container without the edge
    # in front) and must never be the primary source.
    return _canonical_ip(fallback_ip)


def visitor_key(
    headers: dict[str, str] | None,
    fallback_ip: str | None,
    session_id: str | None,
) -> str:
    """A stable, opaque quota bucket for one visitor.

    Pure and separately testable: the Streamlit lookups happen in
    :func:`_visitor_key`, which is the only part that needs a running app.

    The raw address never leaves this function. What comes back is a salted
    digest, which is what reaches the rate limiter, the traces and any log line
    -- so an address cannot be recovered from any of them.
    """
    address = _client_address(headers, fallback_ip)
    if address is not None:
        material = f"ip:{address}"
    elif session_id:
        # Degraded, and deliberately not a shared bucket: with no address at
        # all -- localhost, or a proxy that strips everything -- a per-session
        # key still separates visitors. It is weaker, because a new tab is a
        # new session and therefore a new bucket, so it must never be the
        # normal path. The global backstop still bounds the deployment.
        material = f"session:{session_id}"
    else:
        # Nothing identifying at all. One shared bucket is the conservative
        # answer: it can only refuse more than intended, never less.
        material = "anonymous"

    digest = hashlib.sha256(f"{_VISITOR_SALT}|{material}".encode()).hexdigest()
    return f"visitor:{digest[:32]}"


def _visitor_key() -> str:
    """The quota bucket for the visitor making this request.

    Reads what Streamlit exposes about the connection, never what the page
    sends: a browser-supplied `X-Visitor-Id` would be a request to be given a
    new allowance, and there is no header here a visitor can set for
    themselves. `st.context` is read defensively because a script running
    outside a browser session -- a test, a bare rerun -- has no context.
    """
    headers: dict[str, str] | None = None
    fallback_ip: str | None = None
    session_id: str | None = None
    try:
        headers = dict(st.context.headers)
    except Exception:  # pragma: no cover - no browser session
        headers = None
    try:
        fallback_ip = st.context.ip_address
    except Exception:  # pragma: no cover - no browser session
        fallback_ip = None
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        ctx = get_script_run_ctx()
        session_id = getattr(ctx, "session_id", None) if ctx else None
    except Exception:  # pragma: no cover - no script context
        session_id = None

    return visitor_key(headers, fallback_ip, session_id)


def _run_question(platform: AgentPlatform, question: str) -> None:
    """Run one request and keep everything the result pages need.

    The locale travels with the call. The sentence a visitor reads is written
    by the tool, not by this page, so an English interface that ran the
    platform without saying so would answer in Portuguese -- the one failure
    this whole translation exists to avoid.
    """
    # The progress line, driven by the events the request actually emits.
    #
    # `platform.run()` is synchronous and runs on this very script thread, so
    # writing to a Streamlit element from inside the observer is an ordinary
    # call on the ordinary thread -- no polling, no background worker, no
    # rerun. `st.status` is a native container and is updated through its own
    # API; nothing here touches the DOM, which is the one thing this app has
    # learned not to do.
    #
    # The mapping is from agent identifier to catalogue key, so the labels
    # follow the reader's language while the identifiers stay English and
    # stable. An agent that takes no part in a route never emits
    # `agent_started`, so its line never appears: the progress shown is the
    # progress that happened.
    with st.status(t("progress.router"), expanded=False) as progress:

        def announce(event_type: str, agent: str | None) -> None:
            if event_type == "agent_started" and agent in _PROGRESS_STEPS:
                progress.update(label=t(_PROGRESS_STEPS[agent]))
            elif event_type in _PROGRESS_FINAL:
                progress.update(label=t("progress.finishing"))

        with use_locale(current_locale()), dataset_scope(_visitor_dataset()):
            # The visitor's own bucket, so one enthusiastic reader cannot spend
            # the allowance the next one needs. `quota_scope` carries it to the
            # policy engine, which consults the same bucket the entry point
            # consumed; nothing else about the request changes.
            #
            # And the visitor's own copy of the records: an update one visitor
            # approves changes what *they* read next, not what everyone else
            # sharing this process reads.
            result = platform.run(
                question, quota_key=_visitor_key(), on_event=announce
            )
        progress.update(label=t("progress.done"), state="complete")
    # `events_for_request` already returns plain dicts, which is exactly what
    # `execution_view.build` consumes. Nothing is reshaped here: a second
    # projection of the same rows is a second place for the two to disagree.
    events = platform.repository.events_for_request(result.request_id)
    st.session_state["last_result"] = {
        "question": question,
        "request_id": result.request_id,
        "status": result.status,
        "route": result.route,
        "response": result.response,
        "latency_ms": result.latency_ms,
        "events": events,
        "pending": (
            {
                "tool": result.awaiting_confirmation.tool,
                "arguments": result.awaiting_confirmation.arguments,
                "risk_level": result.awaiting_confirmation.risk_level,
                "reason": result.awaiting_confirmation.reason,
            }
            if result.awaiting_confirmation
            else None
        ),
    }


#: What stopped the request, said in the words of the control that stopped it.
#: Every entry is keyed on a real `blocked_by` value produced by
#: `execution_view._blocked_by`; the dashboard used to attribute all of them to
#: the policy engine, so a visitor who simply clicked too fast was told the
#: security policy had refused them.
#: Keys, not sentences: the words are looked up at render time so the banner
#: follows the reader's language, and the table stays a mapping from control
#: to severity rather than a second place translations live.
_BLOCK_EXPLANATIONS: dict[str, tuple[str, str, str]] = {
    "policy engine": ("error", "block.policy_title", "block.policy_body"),
    "rate limit": ("warning", "block.rate_title", "block.rate_body"),
    "provider budget": ("warning", "block.budget_title", "block.budget_body"),
    "circuit breaker": ("warning", "block.circuit_title", "block.circuit_body"),
    "resource limit": ("warning", "block.limits_title", "block.limits_body"),
}


def _explain_block(last: dict[str, Any], view: execution_view.ExecutionView) -> None:
    """Say which control stopped the request, and stop guessing that it was policy."""
    level, title_key, detail_key = _BLOCK_EXPLANATIONS.get(
        view.blocked_by or "",
        ("error", "block.generic_title", "block.generic_body"),
    )
    banner = st.error if level == "error" else st.warning
    banner(f"**{t(title_key)}**\n\n{t(detail_key)}")


def _next_step(view: execution_view.ExecutionView) -> None:
    """Say what to try next, instead of leaving the visitor to guess.

    The demonstration only lands if someone sees both halves: a request that
    is allowed and one that is refused. After the first, the second is one
    button away rather than something to go looking for.
    """
    if view.outcome is execution_view.State.BLOCKED:
        columns = st.columns([3, 5])
        columns[0].button(
            t("next.see_policies"),
            key="next_policies",
            width="stretch",
            on_click=goto,
            args=("security",),
        )
        columns[1].caption(
            t("next.rule_listed")
        )
        return

    blocked = demo.SECURITY_EXAMPLES[0][0] if demo.SECURITY_EXAMPLES else None
    if blocked is None:
        return
    columns = st.columns([3, 5])
    columns[0].button(
        t("next.try_forbidden"),
        key="next_blocked",
        width="stretch",
        on_click=goto,
        args=("orchestrator", blocked),
    )
    columns[1].caption(
        t("next.contrast")
    )


def _result_panel(platform: AgentPlatform, last: dict[str, Any]) -> None:
    """Everything about the last run, in the order a reader asks for it."""
    view = execution_view.build(last["events"], status=last["status"])

    st.markdown(
        f'<div class="ap-fade">{_outcome_pill(view.outcome)}</div>',
        unsafe_allow_html=True,
    )
    _result_summary(last, view)

    if last["pending"]:
        # The platform's own sentence here says the action needs approval, in
        # English, immediately above a Portuguese panel saying the same thing
        # and offering the buttons. Two messages, one fact. The panel wins: it
        # is the one that can be acted on.
        _confirmation_panel(platform, last)
    else:
        st.markdown(t("result.answer_label"))
        st.markdown(as_prose(last["response"] or ""))

    if view.outcome is execution_view.State.OUT_OF_SCOPE:
        st.info(
            t("result.out_of_scope"),
            icon=":material/help:",
        )
    elif view.outcome is execution_view.State.BLOCKED:
        _explain_block(last, view)

    if not last["pending"]:
        _next_step(view)

    # The timeline stays on the surface. It is the demonstration: the policy
    # decision sitting in sequence between the proposal and the tool call is
    # the thing this project exists to show, and a reader will not open an
    # expander to find an argument nobody made to them.
    st.subheader(t("result.how_processed"), anchor=False)
    _timeline(view)
    _technical_details(last, view)


#: What the question field accepts. Mirrors `Settings.max_question_chars`,
#: which is the authority; this side is the physical stop in the browser.
QUESTION_MAX_CHARS: Final[int] = DEFAULT_MAX_QUESTION_CHARS


def page_orchestrator(platform: AgentPlatform) -> None:
    eyebrow(t("orch.eyebrow"))
    st.title(t("orch.title"), anchor=False)
    lede(
        t("orch.lede")
    )

    queued = st.session_state.pop("queued_question", None)
    if queued is not None:
        st.session_state["question_box"] = queued

    # `max_chars` is the physical stop and it brings its own live counter.
    #
    # Measured in the browser rather than assumed: Streamlit does *not* emit a
    # `maxlength` attribute here -- the input reports `maxLength === -1` -- it
    # enforces the ceiling in its own change handler. The effect is the one
    # that matters: 95 keystrokes then ten more leaves the field at 100, and a
    # 150-character paste arrives as 100. The widget also renders `100/100`
    # beside the field and updates it as somebody types.
    #
    # A second, hand-written counter was tried here and removed. Streamlit
    # re-renders the input from its own state, so a counter reading the DOM
    # went stale -- it showed `156/100` while the field held nothing. A count
    # that can lie about the field is worse than the one the widget already
    # draws truthfully.
    #
    # `max_chars` counts UTF-16 units where Python counts code points, so for
    # an emoji it refuses *earlier* than the backend would, never later. The
    # backend stays the authority.
    st.text_input(
        t("orch.question"),
        key="question_box",
        max_chars=QUESTION_MAX_CHARS,
        placeholder=t("orch.placeholder"),
        label_visibility="collapsed",
    )

    budget = demo_budget.budget_state(
        platform.repository, live=platform.provider_info.live
    )
    run_columns = st.columns([1, 5])
    run_clicked = run_columns[0].button(
        t("orch.run"), type="primary", key="run_question", width="stretch",
        disabled=budget.exhausted,
    )
    run_columns[1].caption(budget.message)

    if run_clicked:
        question = (st.session_state.get("question_box") or "").strip()
        if question:
            # The progress container lives inside `_run_question`, next to the
            # events that drive it. It replaces the spinner that used to be
            # here: a spinner said only "something is happening", which for a
            # request that can spend a minute retrying against a busy provider
            # is indistinguishable from a page that has stopped responding.
            _run_question(platform, question)

    # The result comes before the examples. It used to come after them, which
    # meant clicking Executar scrolled nothing and the answer appeared below
    # twelve example questions -- the one thing the visitor came for was the
    # last thing on the page.
    last: dict[str, Any] | None = st.session_state.get("last_result")
    if last:
        st.divider()
        _result_panel(platform, last)
        st.divider()

    st.header(t("orch.examples"), anchor=False)
    read_tab, action_tab, security_tab = st.tabs(
        [t("examples.read_tab"), t("examples.action_tab"), t("examples.security_tab")]
    )
    for tab, group, note, prefix in (
        (read_tab, demo.READ_ONLY_EXAMPLES, t("examples.read_note"), "r"),
        (
            action_tab,
            demo.ACTION_EXAMPLES,
            t("examples.action_note"),
            "a",
        ),
        (
            security_tab,
            demo.SECURITY_EXAMPLES,
            t("examples.security_note"),
            "s",
        ),
    ):
        with tab:
            st.caption(note)
            for index, (question, hint) in enumerate(group):
                columns = st.columns([6, 2])
                columns[0].code(question, language="text", wrap_lines=True)
                columns[0].caption(hint)
                # A stable, content-independent key. The previous version used
                # `id(examples)`, a memory address -- it changes when the module
                # is reloaded, so every widget got a new identity in the same
                # position and React was asked to move nodes that no longer
                # existed. That was the `insertBefore` error.
                columns[1].button(
                    t("company.ask"),
                    key=f"ex_{prefix}_{index}",
                    width="stretch",
                    on_click=goto,
                    args=("orchestrator", question),
                )


# ============================================================== 4. Segurança


def page_security(platform: AgentPlatform) -> None:
    eyebrow(t("security.eyebrow"))
    st.title(t("nav.security"), anchor=False)
    lede(
        t("security.lede")
    )

    st.header(t("security.four_pillars"), anchor=False)
    cards(
        [
            (t("pillar.auth"), t("pillar.auth_body")),
            (t("pillar.authz"), t("pillar.authz_body")),
            (t("pillar.policy"), t("pillar.policy_body")),
            (t("pillar.audit"), t("pillar.audit_body")),
        ],
        per_row=4,
    )

    st.header(t("security.three_decisions"), anchor=False)
    decisions = st.columns(3)
    decisions[0].markdown(
        f'{_decision_pill("ALLOW")}<p class="ap-lede">{t("decision.allow_body")}</p>',
        unsafe_allow_html=True,
    )
    decisions[1].markdown(
        f'{_decision_pill("REQUIRE_CONFIRMATION")}<p class="ap-lede">'
        f'{t("decision.confirm_body")}</p>',
        unsafe_allow_html=True,
    )
    decisions[2].markdown(
        f'{_decision_pill("DENY")}<p class="ap-lede">{t("decision.deny_body")}</p>',
        unsafe_allow_html=True,
    )

    st.header(t("security.try_hostile"), anchor=False)
    lede(
        t("security.scenarios_lede")
    )
    for index, scenario in enumerate(demo.SCENARIOS):
        # The level and the title head the whole row instead of sitting inside
        # its left column. Inside it they pushed the question 83px down while
        # the button, alone in the right column, stayed at the top of the row --
        # measured -- so the control that runs a question sat level with the
        # level tag rather than with the question. Above the columns, the two
        # start on the same line, which is how this same pair already reads on
        # Empresa and on Orquestrador.
        st.markdown(
            f'<span class="ap-eyebrow">{scenario["level"]}</span>',
            unsafe_allow_html=True,
        )
        st.markdown(f"**{scenario['title']}**")
        columns = st.columns([6, 2])
        columns[0].code(scenario["ask"], language="text", wrap_lines=True)
        columns[0].caption(scenario["expect"])
        columns[1].button(
            t("orch.run"),
            key=f"scenario_{index}",
            width="stretch",
            on_click=goto,
            args=("orchestrator", scenario["ask"]),
        )
        # `watch` is what to look at in the trace once it has run, so it sits
        # one disclosure away rather than competing with the question itself.
        with columns[0].expander(t("security.what_to_watch")):
            st.caption(scenario["watch"])

    with st.expander(t("security.rules_in_full")):
        st.dataframe(describe_rules(), width="stretch", hide_index=True)

    with st.expander(t("security.who_may_call")):
        st.caption(
            t("security.matrix_lede")
        )
        st.dataframe(describe_matrix(), width="stretch", hide_index=True)


# ============================================================ 5. Arquitetura


def page_architecture(platform: AgentPlatform) -> None:
    eyebrow(t("arch.eyebrow"))
    st.title(t("nav.architecture"), anchor=False)
    lede(
        t("arch.lede")
    )

    st.header(t("arch.request_path"), anchor=False)
    flow(
        [
            "User",
            "API",
            "Router",
            t("flow.agents"),
            "Policy Engine",
            "Tools / MCP",
            "Data / RAG",
        ],
        gate="Policy Engine",
    )

    # Two lines, and the difference between them is the honest part. The first
    # is this installation; the second is where a customer's systems would
    # attach, which is not built and says so in its own lede rather than in a
    # footnote a reader has to go looking for.
    st.header(t("arch.current"), anchor=False)
    lede(t("arch.current_lede"))
    flow(
        [
            demo.COMPANY_NAME,
            t("flow.simdata"),
            demo.PRODUCT_NAME,
            t("flow.agents"),
            "Policy Engine",
            "Gateway",
            t("flow.tools_plural"),
        ],
        gate="Policy Engine",
    )

    st.header(t("arch.production"), anchor=False)
    lede(t("arch.production_lede"))
    flow(
        [
            t("flow.realco"),
            t("flow.sources"),
            t("flow.layer"),
            demo.PRODUCT_NAME,
            t("flow.agents"),
            "Policy Engine",
            "Gateway",
            t("flow.authtools"),
        ],
        gate="Policy Engine",
    )

    # Four groups, in the order the request meets them, because the one
    # distinction this page exists to make is which of these things is an agent.
    # Agents come first and the governance layer follows immediately, so the
    # sentence "none of these is an agent" lands next to the list it is about.
    st.header(t("arch.the_agents"), anchor=False)
    lede(t("arch.agents_lede"))
    for role in demo.AGENT_ROLES:
        st.markdown(f"**{role['title']}** — {role['job']}")
        st.caption(role["holds"])

    st.header(t("arch.group_governance"), anchor=False)
    lede(t("arch.group_governance_lede"))
    cards(
        [
            (t("svc.policy"), t("svc.policy_body")),
            (t("svc.gateway"), t("svc.gateway_body")),
            (t("svc.mcp"), t("svc.mcp_body")),
        ]
    )
    st.info(
        t("arch.policy_not_agent"),
        icon=":material/gavel:",
    )

    st.header(t("arch.group_data"), anchor=False)
    lede(t("arch.group_data_lede"))
    cards(
        [
            (t("data.dataset"), t("data.dataset_body")),
            (t("data.kb"), t("data.kb_body")),
        ]
    )

    st.header(t("arch.group_infra"), anchor=False)
    cards(
        [
            (t("platform.k8s"), t("platform.k8s_body")),
            (t("platform.redis"), t("platform.redis_body")),
            (t("platform.postgres"), t("platform.postgres_body")),
            (t("platform.netpol"), t("platform.netpol_body")),
            (t("platform.tls"), t("platform.tls_body")),
        ]
    )

    st.header(t("arch.group_observability"), anchor=False)
    cards(
        [
            (t("obsv.tracing"), t("obsv.tracing_body")),
            (t("obsv.metrics"), t("obsv.metrics_body")),
            (t("obsv.logs"), t("obsv.logs_body")),
        ]
    )

    # Only what the landing page did not already show. Rendering the whole
    # list here repeated four cards word for word for anyone who arrived from
    # the first screen -- the same claims, twice, two clicks apart. The slice
    # is taken from the same declared source, so the two pages cannot drift:
    # adding a capability to `DEMONSTRATED` still puts it on exactly one of
    # them, decided by its position rather than by a second list.
    remaining = list(demo.DEMONSTRATED[OVERVIEW_CAPABILITIES:])
    if remaining:
        st.header(t("arch.also_demonstrated"), anchor=False)
        lede(t("arch.also_lede"))
        cards(remaining, per_row=2)


# ========================================================= 6. Observabilidade


#: How far back the headline median reads. Above this installation's whole
#: history, and still one bounded query rather than an unbounded scan.
LATENCY_SAMPLE = 500

#: Below three samples a median is arithmetic rather than evidence.
LATENCY_MIN_SAMPLES = 3


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _median_latency_for_mode(platform: AgentPlatform) -> tuple[float, int] | None:
    """The median latency of the mode the visitor is actually looking at.

    The headline used to be a mean over every request ever recorded, and that
    mixed two populations two orders of magnitude apart: the deterministic stub
    answers in about a tenth of a second, a live model in about fourteen. A mean
    over both described neither, and a median over both would only have changed
    which population won.

    So it filters. `provider` is the column the platform already writes, from
    `self.provider.name`, and `provider_info.name` returns that same string --
    the badge on screen and the rows counted here read one value, so they cannot
    drift apart. It returns `None` rather than a number when the current mode has
    too few requests to have a median worth printing, because the alternative is
    to fall back to the mixed population, which is the thing being fixed.
    """
    mode = platform.provider_info.name
    samples = [
        float(row["latency_ms"])
        for row in platform.repository.recent_requests(limit=LATENCY_SAMPLE)
        if row.get("provider") == mode and row.get("latency_ms") is not None
    ]
    if len(samples) < LATENCY_MIN_SAMPLES:
        return None
    return _median(samples), len(samples)


def _latency_chart(rows: list[dict[str, Any]]) -> None:
    """Per-request latency, with the one number that explains its shape.

    The median is shown beside the chart because a single suspended request
    dominates the axis: a request that stops for human approval keeps counting
    wall-clock time while it waits, so its latency is mostly the time a person
    took to decide. That is worth stating rather than smoothing away -- the
    chart is evidence, and evidence that has been tidied is not evidence.
    """
    latencies = [float(row.get("latency_ms") or 0) for row in reversed(rows)]
    if not any(latencies):
        st.caption(
            t("obs.no_latency")
        )
        return

    median = _median(latencies)

    st.caption(t("obs.latency_caption"))
    st.altair_chart(
        alt.Chart(alt.Data(values=[
            {"n": index, "ms": value} for index, value in enumerate(latencies)
        ]))
        .mark_line(point=True, color="#2F81F7")
        .encode(
            # Explicit domains: see the note above. Both are read from the
            # series being drawn.
            x=alt.X(
                "n:Q",
                title=None,
                scale=alt.Scale(domain=[0, max(1, len(latencies) - 1)]),
            ),
            y=alt.Y(
                "ms:Q",
                title="ms",
                scale=alt.Scale(domain=[0, max(latencies)]),
            ),
        )
        # `width="container"` belongs in the Vega spec, not only in the
        # Streamlit call. `width="stretch"` below stretches the *element*;
        # Vega-Lite still drew at its own 400px default plus padding, which
        # measured 423px inside a 390px phone viewport and put 50px of the plot
        # past the right edge with no page scroll to reach it. Only the spec can
        # tell Vega to read the container. Height stays fixed: it is the axis
        # that carries the data, and it fits.
        .properties(height=180, width="container"),
        width="stretch",
    )
    st.caption(
        t("obs.latency_note", median=f"{median:.0f}", peak=f"{max(latencies):.0f}")
    )


def page_observability(platform: AgentPlatform) -> None:
    eyebrow(t("arch.platform"))
    st.title(t("nav.observability"), anchor=False)
    lede(
        t("obs.lede")
    )
    st.caption(t("obs.real_vs_demo"))

    try:
        metrics = collect_metrics(platform.repository)
    except Exception:
        metrics = None

    if metrics is None or not metrics.has_data:
        no_data(t("obs.none_recorded"))
        return

    columns = st.columns(4)
    columns[0].metric(t("obs.requests"), metrics.requests)
    columns[1].metric(t("obs.blocked"), metrics.blocked)
    columns[2].metric(t("obs.tool_calls"), metrics.tool_calls)
    mode = platform.provider_info.name.upper()
    measured = _median_latency_for_mode(platform)
    columns[3].metric(
        t("obs.median_latency", mode=mode),
        f"{measured[0]:.0f} ms" if measured else "—",
        help=(
            t("obs.median_latency_help", samples=measured[1], mode=mode)
            if measured
            else t("obs.median_latency_none", minimum=LATENCY_MIN_SAMPLES, mode=mode)
        ),
    )

    st.header(t("obs.recent_requests"), anchor=False)
    rows = platform.repository.recent_requests(limit=12)
    if not rows:
        no_data(t("obs.no_recent"))
        return

    # Column order is the mobile fix, and nothing is dropped to achieve it.
    #
    # The table scrolls inside its own container, so a narrow screen does not
    # lose a column -- it puts one out of sight behind a sideways swipe most
    # visitors will not try. Measured at 390px the cut fell mid-`retries`, and
    # `ms` was entirely off-screen: the latency, on the page about latency.
    #
    # Which columns are reachable without scrolling is decided by their order,
    # and that order is the same on every screen, so this needs no breakpoint
    # and no second rendering path. Identifier, outcome and duration come
    # first; route and retry count follow, and on desktop and tablet all five
    # are visible exactly as before.
    st.dataframe(
        [
            {
                t("obs.col_request"): row["request_id"],
                t("obs.col_status"): row["status"],
                t("obs.col_ms"): round(float(row.get("latency_ms") or 0), 1),
                t("obs.col_route"): row.get("route") or "—",
                # Recorded per request all along and never shown. "Was there a
                # retry" is one of the questions this page is supposed to answer.
                t("obs.col_retries"): int(row.get("retry_count") or 0),
            }
            for row in rows
        ],
        width="stretch",
        hide_index=True,
    )

    _latency_chart(rows)

    with st.expander(t("obs.details")):
        st.caption(
            t("obs.counters_lede")
        )
        summary = platform.repository.metrics_summary()
        st.dataframe(
            # `metrics_summary` mixes Decimal and int in one column, and
            # pyarrow refuses a column of mixed types. These are display
            # values, so coercing them to text loses nothing and keeps the
            # table from raising on a spend figure.
            [
                {t("obs.metric"): key, t("obs.value"): str(value)}
                for key, value in sorted(summary.items())
            ],
            width="stretch",
            hide_index=True,
        )
        st.caption(pricing_notice())


# ================================================================ navigation


#: Six pages. The order is the order a first-time reader should meet them.
#: Pages are keyed by slug, never by the label a reader sees.
#:
#: The navigation radio's key *is* `st.session_state["nav"]`, so whatever the
#: options are is what the session stores. Keyed by label, switching language
#: would leave `nav` holding "Segurança" while the options had become English,
#: the guard below would find it absent, and the reader would be silently
#: dropped back on the first page mid-visit. A slug does not change, so the
#: page survives the switch -- which is what `test_i18n.py` pins.
#: A glyph per page of the guided journey. The three platform pages keep the
#: shared marker instead, because "these three are instruments, not the tour"
#: is worth more than three more icons -- and it is the property
#: `test_navigation_puts_the_demo_before_the_technical_pages` pins: the groups
#: must be distinguishable without reading the label above the list.
#:
#: Plain characters rather than an icon font: a font is another request, and
#: this project does not make requests it did not declare.
NAV_GLYPHS: dict[str, str] = {
    "overview": "◇",
    "company": "▤",
    "orchestrator": "◈",
    "security": "⛨",
    "architecture": "⊞",
}

#: What the platform pages carry instead.
NAV_PLATFORM_GLYPH = "⚙"

DEMO_PAGES = {
    "overview": page_overview,
    "company": page_company,
    "orchestrator": page_orchestrator,
    "security": page_security,
    "architecture": page_architecture,
}

PLATFORM_PAGES = {
    "observability": page_observability,
}

PAGES = {**DEMO_PAGES, **PLATFORM_PAGES}


def _set_locale(code: str) -> None:
    """The picker's only side effect.

    `LOCALE_KEY` stays exactly what it was -- the single value `current_locale`
    reads -- so nothing about the translation mechanism changes along with the
    control that writes it.
    """
    st.session_state[LOCALE_KEY] = code


def _language_picker() -> None:
    """Closed it shows the language in use; open it shows both.

    The design asks for flag emoji, and Windows has no glyph for one: Chrome
    draws a regional-indicator pair as two boxed letters. Measured in this
    browser, "🇧🇷" is 30px wide against 15px for a lone
    indicator -- exactly double, so the pair never fused and the control would
    have read "BR" and "US". The flags are drawn as inline SVG in the
    stylesheet instead, which keeps a flag a flag without a font to fetch: the
    dashboard loads nothing over the network and this does not become the one
    exception.

    A popover and two buttons rather than a `selectbox`, because then every
    hook the stylesheet needs is a `st-key-` class this project owns -- the
    same reason the navigation radio is styled through its key and not through
    the emotion hash beside it. A `selectbox` would have meant styling BaseWeb's
    internals, and its options cannot carry a drawn flag at all.

    Streamlit closes a popover on the next rerun and a button click is a rerun,
    so selecting a language shuts the panel without anything here doing it.
    """
    current = st.session_state[LOCALE_KEY]
    st.sidebar.markdown(
        f'<p class="ap-langlabel">{t("nav.language")}</p>', unsafe_allow_html=True
    )
    # The key carries the locale so which flag the closed control shows is a
    # static rule in the stylesheet, not a `<style>` injected on every rerun --
    # an injected one would leave a stray element in the sidebar. `type` marks
    # the language in use; Streamlit stamps it as `kind="primary"`, which is
    # the hook the check mark hangs off.
    with st.sidebar.popover(
        LOCALE_LABELS[current], width="stretch", key=f"lang_open_{current}"
    ):
        # The language in use heads the list, which is how the design draws
        # it in both states -- open on Portuguese and open on English.
        for code in (current, *(c for c in LOCALES if c != current)):
            st.button(
                LOCALE_LABELS[code],
                key=f"lang_opt_{code}",
                width="stretch",
                type="primary" if code == current else "secondary",
                on_click=_set_locale,
                args=(code,),
            )


def _declare_language() -> None:
    """Declare the page's language, and refuse machine translation of it.

    Streamlit serves ``<html lang="en">`` and offers no way to change it. The
    dashboard is read in Portuguese or in English, and a page whose declared
    language disagrees with its words is what invites Chrome to translate it
    by itself -- which rewrites the DOM under
    React's feet, wrapping text nodes in elements React never created. React
    then unmounts a subtree on the next navigation, calls ``removeChild`` on a
    node the translator has reparented, and the reader gets

        NotFoundError: Failed to execute 'removeChild' on 'Node':
        The node to be removed is not a child of this node.

    printed where the page should be. Confirmed in an incognito window: the
    error appears with translation on and does not appear with it off, and
    ~170 navigations with an untouched DOM never produced it.

    ``lang`` follows the chosen locale so a screen reader says the words the
    way they are written. ``translate="no"`` does **not**: it is unconditional
    in both languages. Offering a language of our own is exactly the argument
    that would make dropping it feel safe, and it is the wrong moment to --
    a reader on the English page can still ask Chrome to translate it into a
    third language, and that is the same DOM rewrite with the same crash.

    A ``<script>`` inside ``st.markdown`` cannot do this -- Streamlit does not
    execute script tags in ``unsafe_allow_html``. ``components.v1.html`` renders
    a same-origin iframe that does run JavaScript, which is the documented way
    to reach the host document, so the two attributes are set from there.
    """
    tag = "en" if current_locale() == "en" else "pt-BR"
    components.html(
        "<script>"
        "const root = window.parent.document.documentElement;"
        f"root.lang = '{tag}';"
        "root.setAttribute('translate', 'no');"
        "</script>",
        height=0,
    )


def _scroll_main_to_top(token: str) -> None:
    """Put the reader back at the top after a page change.

    Navigation here is a Streamlit rerun of the same script, not a URL change:
    no hash, no history entry, so the browser's own scroll restoration is not
    involved. The DOM is patched in place and whatever offset the previous page
    left behind simply stays.

    The element to reset is `section[data-testid="stMain"]`, which carries
    `overflow-y: auto`. The window does not scroll at all here -- measured:
    `window.scrollY` is 0 while `stMain.scrollTop` was 1412 -- so
    `window.scrollTo` would do nothing. `components.v1.html` renders a
    same-origin iframe and is the only place this app can execute JavaScript,
    which is the same mechanism `_declare_language` uses.

    `token` goes into the markup so consecutive navigations produce different
    HTML; identical content would let Streamlit reuse the iframe and never
    re-run the script. The second pass on the next frame is not belt and
    braces: Streamlit paints the new page progressively, and a container that
    is still growing can end up scrolled by the browser after the first pass.
    """
    components.html(
        "<script>"
        "const doc = window.parent.document;"
        "const put = () => {"
        "  const main = doc.querySelector('section[data-testid=\"stMain\"]');"
        "  if (main) main.scrollTop = 0;"
        "};"
        "put();"
        "requestAnimationFrame(() => { put(); requestAnimationFrame(put); });"
        f"/* {token} */"
        "</script>",
        height=0,
    )


def main() -> None:
    _declare_language()
    st.markdown(_STYLE, unsafe_allow_html=True)
    platform = get_platform()

    # Brand, category, state -- the three things a console says about itself
    # before it says anything about the work. The mark is drawn in CSS; an
    # image would be another asset to load and another request to justify.
    st.sidebar.markdown(
        f'<div class="ap-brand"><span class="ap-brandmark">N</span>'
        f'<span class="ap-brandname">{demo.PRODUCT_NAME}</span></div>'
        f'<div class="ap-brandcat">{demo.PRODUCT_CATEGORY}</div>'
        f'<span class="ap-status"><i></i>{t("sidebar.status")}</span>',
        unsafe_allow_html=True,
    )

    # Above the mode badge and above the navigation, because it governs both.
    # Keyed on `LOCALE_KEY`, so the widget *is* the locale -- the same
    # arrangement the navigation radio uses, and for the same reason.
    if st.session_state.get(LOCALE_KEY) not in LOCALES:
        st.session_state[LOCALE_KEY] = DEFAULT_LOCALE
    _language_picker()

    options = list(PAGES)
    # `nav` is the page the reader is on. The radio that sets it is keyed *per
    # locale*, and the two are deliberately separate things.
    #
    # One widget for both languages does not work: Streamlit's frontend marks
    # the selected option by its rendered label, so the moment `format_func`
    # started returning English the selection matched nothing and the sidebar
    # showed the reader on no page at all -- while the correct page rendered
    # beside it, because the Python side had `nav` right the whole time.
    # Reproduced by switching to English on the Security page.
    #
    # A key per locale gives each language its own widget, seeded from `nav`
    # before it is built and writing back to `nav` when it changes. `nav` never
    # holds a translated label, so the page still survives the switch, which is
    # the property `test_i18n.py` pins.
    if st.session_state.get("nav") not in options:
        st.session_state["nav"] = options[0]

    nav_widget = f"nav_{current_locale()}"
    st.session_state[nav_widget] = st.session_state["nav"]

    def remember() -> None:
        st.session_state["nav"] = st.session_state[nav_widget]

    def label(slug: str) -> str:
        """The reader's word for a page, from the slug the session stores.

        The journey pages get their own glyph; the instruments share one, so
        the two groups stay distinguishable inside the list rather than only
        from the section labels above and below it.
        """
        glyph = NAV_GLYPHS.get(slug, NAV_PLATFORM_GLYPH)
        return f"{glyph}  {t(f'nav.{slug}')}"

    st.sidebar.markdown(
        f'<p class="ap-navlabel">{t("nav.demo_group")}</p>', unsafe_allow_html=True
    )
    # The label is deliberately not translated: Streamlit derives widget
    # identity from its parameters, the label included, and it is collapsed so
    # nobody reads it on screen.
    choice = st.sidebar.radio(
        "Page",
        options,
        key=nav_widget,
        format_func=label,
        label_visibility="collapsed",
        on_change=remember,
    )
    st.sidebar.markdown(
        f'<p class="ap-navnote">{t("nav.platform_group")}</p>',
        unsafe_allow_html=True,
    )

    # Which engine is answering, and whose data it is answering about. One card
    # rather than four loose captions: a stranger asks both questions at once.
    #
    # `st.warning` paints an olive box in the dark theme, which reads as a
    # problem. Running without a provider is the default and correct state of
    # this demo, not a fault -- so it gets a neutral badge. Amber stays
    # reserved for warnings and green for success, which is what makes either
    # of them mean anything.
    info = platform.provider_info
    badge = (
        '<span class="ap-pill ap-allow">LIVE</span>'
        if info.live
        else '<span class="ap-pill ap-hold">DEMO / STUB</span>'
    )
    st.sidebar.markdown(
        f'<p class="ap-navlabel">{t("sidebar.group_env")}</p>'
        f'<div class="ap-env"><div class="ap-env-row">{badge}</div>'
        f"<code>{info.model}</code>"
        f'<p>{t("mode.live_caption") if info.live else t("mode.stub_caption")}</p>'
        f'<p>{t("sidebar.env_fictional", company=demo.COMPANY_NAME)}</p></div>',
        unsafe_allow_html=True,
    )

    # Only on a change of page. Every rerun passes through here -- running a
    # question on the Orquestrador is one -- and yanking the reader to the top
    # mid-interaction would be a worse bug than the one being fixed.
    if st.session_state.get("_scrolled_to") != choice:
        st.session_state["_scrolled_to"] = choice
        _scroll_main_to_top(choice)

    # Each page in a container keyed by its own slug.
    #
    # Navigation is a rerun, and React reconciles the element tree *in place*:
    # it matches the previous page's children to the new page's by position and
    # patches them one by one. Measured during a transition, 27 to 47 of the
    # previous page's containers are still mounted and marked `data-stale`
    # while the new page is being built. They are still painted, and `.ap-fade`
    # brings new cards up from `opacity: 0` over 160ms -- so for as long as the
    # reconciliation runs, the old page shows *through* the new one.
    #
    # A key that changes with the page makes React unmount the whole previous
    # subtree instead of reusing it, so there is nothing left underneath to
    # show through. This is a lifecycle fix, not a coat of CSS over the symptom.
    with st.container(key=f"page-{choice}"):
        PAGES[choice](platform)


main()
