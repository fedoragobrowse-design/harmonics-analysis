---
version: alpha
name: Harmonics Analysis
description: A spectral field-notebook site that makes the hidden structure of a held note visible.
colors:
  background: "#0A1022"
  surface: "#111B34"
  panel: "#162B57"
  line: "#536482"
  signal: "#7DA6FF"
  harmonic: "#FFA36E"
  positive: "#9AD6C2"
  ink: "#F4F1E9"
typography:
  display: { fontFamily: "Fraunces, Georgia, serif" }
  body: { fontFamily: "Manrope, Arial, sans-serif" }
  utility: { fontFamily: "DM Mono, ui-monospace, monospace" }
rounded:
  DEFAULT: "0"
spacing:
  page-max: "73.75rem"
  section-gap: "7.75rem"
components:
  screenshot: { border: "1px solid rgba(125,166,255,.45)", offsetShadow: "16px 18px 0 #162B57" }
  button: { shape: "11px clipped corners" }
  card: { surface: "#111B34", separator: "1px solid #536482" }
---

# Harmonics Analysis Design System

## Overview

### Creative North Star

An annotated spectral field notebook: a musician's working score crossed with an audio-lab instrument panel. The site should make a harmonic stack feel observed and named, rather than marketed.

### Product context and register

- **Audience and primary job:** singers, teachers, musicians, and curious listeners deciding whether Harmonics Analysis will help them understand a held sound.
- **Target market(s) and evidence:** global English-language open-source audience; the repository's README and application copy are English.
- **Locale(s) and language policy:** English only at present. UI copy uses plain vocabulary before technical terms.
- **Usage scene:** a desktop or phone visit before downloading, often adjacent to a practice session or an audio file.
- **Register:** brand/content site; it explains a local desktop application rather than reproducing the app workflow.
- **Memorable signature:** the harmonic rail—warm guide marks against a deep-blue spectral field, repeated only in the hero and visual captions.
- **Restraint:** paragraphs, documentation links, and use cases stay practical and unadorned.
- **Anti-references:** generic startup gradients, anonymous glass cards, and a retro oscilloscope imitation. The application itself owns the live spectrum; the site frames it like readable musical evidence.
- **Token ownership/runtime mapping:** this document defines the public-site tokens, implemented directly as CSS custom properties in `site/spectral.css`. There is no generated token artifact.

## Colors

`background` and `surface` are a blue-black paper field; `harmonic` marks overtone information and the primary download action. `signal` is reserved for measured/spectrum detail. `positive` signals privacy or local processing. `ink` is primary text and `line` carries quiet structure. The pale showcase is an alternate measurement surface, not a second theme.

## Typography

Fraunces is reserved for headings and document-link titles. Manrope carries explanatory copy. DM Mono labels measurement-like information, captions, and navigation. System fallbacks keep the site readable if web fonts fail. Sentence case is standard; uppercase stays in compact labels.

## Layout

The site uses a 73.75rem measure and a two-column hero that becomes one reading column below 830px. Wide section gaps establish separate observations, rather than a feature-grid rhythm. SVG interface visuals reserve their own geometry before loading. The document owns visible themed scrollbars.

## Elevation & Depth

Static content is flat. Interface visuals alone receive a square blue or pale-green offset shadow, as if clipped lab references. No blur, glass, or soft card shadow is used.

## Shapes

Rules, right angles, and square panels dominate. Action controls have one clipped corner: a restrained allusion to a folded analysis note. Pills are not used.

## Components

### Foundational visual states

Links and actions have mint focus rings. Hover adds color or a small stable lift and never hides essential information. Reduced-motion preferences remove transitions. This static site has no forms, loading, error, or overlay states.

### Buttons and actions

The apricot solid button is the sole download action. The outlined action navigates within the page. Documentation rows are links because they navigate.

### Navigation and data display

The desktop navigation exposes only anchors and source. At small widths content remains linearly accessible. Interface visuals have descriptive alternative text and captions.

### Motion

The only animation is a small card lift on hover. No decorative loops or autoplaying movement are used.

### Content and data visualization

Technical claims use concrete terms: H1–H6, 55–1,200 Hz, local processing. Spectrum blue, overtone amber, and privacy mint retain the meaning established by the desktop application.

## Do's and Don'ts

- **Do:** use guides, rules, labels, and numbered steps as information-carrying notation.
- **Do:** keep application visuals as the site's central evidence.
- **Don't:** add generic metrics, badges, gradients, or decorative waveform art.
- **Don't:** use color as the only explanation of spectrum or privacy meaning.
