# We didn't read about these problems in a case study. We lived them.

## Where this comes from

Every great product starts with a scar. Ours came from years spent inside
**food & beverage and hospitality operations** — the industries where the margins
are thin, the volume is enormous, and the difference between a good month and a
bad one hides in details no one sees until it's too late.

Between us we have sat in every seat that feels this pain:

- **The operations seat** — the person who got the call that a store had been
  effectively closed since lunch, and only found out at the end-of-day report.
- **The finance seat** — reconciling variances a day and a half after the money
  already walked out the door, with no way to tell a real problem from ordinary
  weather-and-weekend noise.
- **The detection seat** — years at **Binary Defense**, a cybersecurity company whose
  entire craft is *catching an anomaly fast and responding before it becomes a loss.*
- **The builder's seat** — Microsoft-certified software engineering and formal
  **business analysis (IIBA)**, the discipline of turning messy operational pain into
  software that actually ships.
- **On the ground in Vietnam** — not observing the market from abroad, but *in it.*

And we didn't learn this in one place. Combined, we've worked at names you know —
**Starbucks, Apple, GE, Geico**, and others — in roles that span the full range, from
**leadership all the way down to hands-on engineering**. We've operated F&B at scale,
carried finance and operations accountability, and written the code underneath. We've
seen what companies of that caliber measure, reward, and worry about.

In short: **we know what companies value.**

That combination is not an accident. It's the whole point.

## The team

We are the three founders of **Secure Insights AI** — three angles on the same problem.

**Jason Koo — co-founder.** Former **CIO of Vinh Hoan Corporation**, a publicly traded
Vietnamese company, and most recently **Director of Engineering at Binary Defense**. He
has carried both the executive accountability for enterprise systems *and* the
threat-detection craft of catching problems before they become losses. He's the one who
spent years asking *"why are we the last to know?"* — and finally built the answer.

**Kevin Yee — co-founder.** Former **Vietnam Country Manager for Apple**, and **Chief
Growth Officer / Chief Marketing Officer** at CoderSchool and POPS Worldwide. He has led
market, growth, and P&L for major brands across Vietnam — so he knows exactly what
leadership needs to see, and keeps the platform honest about how the business actually
runs on the ground.

**Christian Decker — co-founder · engineering & business analysis.** A software engineer
and business analyst who turns operational pain into software that ships — the bridge
from *"here's what hurts"* to *"here's the working system."*

We founded **Secure Insights AI** to close the gap we lived. Across our careers our
résumés run through **Starbucks, Apple, GE, and Geico** — from leadership roles to
hands-on engineering. We've felt this problem from the top of the org chart and from
inside the codebase.

## What the past taught us

Three lessons, learned the hard way, shaped everything we built.

**1. In operations, latency is loss.** In security you learn a brutal truth: the
damage of an incident is roughly the *rate* of harm multiplied by the *time* you stay
blind to it. Restaurants are no different. If a store earns revenue at rate **r** and an
issue goes undetected for a time **Δt**, the loss is approximately:

> **loss  ≈  Σ rᵢ · Δtᵢ**  — summed across every incident *i*

Leadership had gotten very good at shrinking **r** (better menus, better throughput)
and completely stuck on **Δt**. The industry standard for Δt was **24–48 hours** — a
manual report, read the next morning. We kept asking: *why are we the last to know?*

**2. Without a baseline, everything looks like noise.** The hardest question in a
daily review isn't "did sales drop?" — it's *"is this drop a problem, or just a rainy
Tuesday after a holiday?"* Finance teams burned days chasing variances that were
simply expected seasonality, and missed the ones that mattered. What was missing was a
**predictive baseline**: a model of what *should* have happened, so a genuine anomaly
is the point where reality breaks away from expectation:

> **it's a real anomaly when  |actual − expected| > z · σ**
> *(the gap exceeds normal day-to-day variation)*

No baseline — no signal, just a haystack.

**3. Finding the problem is not the same as fixing it.** Even when someone finally
spotted an issue, the answer was a question: *why?* Which channel, which daypart, which
delivery partner, which store? That root-cause hunt is where the real analyst hours
died — and where a POS outage or a payment-gateway failure quietly kept bleeding.

## How we're solving it

We built the system we *wished* we'd had in those seats. It attacks all three lessons
directly.

**It kills the latency (Δt).** Instead of a next-day report, a
**machine-learning time-series model** learns each store's demand rhythm — its trends,
weekly cycles, and seasonality — and forecasts it for every store, item, and channel.
The platform then watches reality against that live forecast continuously, surfacing an
issue in **under two hours** — a **>90% reduction** in time-to-detect versus the 24–48
hour status quo.

**It gives finance the baseline it never had.** A machine-learning time-series
forecasting engine learns what *normal* looks like — day-of-week, weather, promotions,
Tết and Vietnamese holidays, new-store ramp-up — accurate to a daily store-level error
of:

> **MAPE = average( |actual − forecast| ÷ actual ) = 9.1%**  (target ≤ 10%)

Now a "drop" is measured against expectation, not guessed against last week.

**It answers *why*, automatically.** Every anomaly ships with its **top contributing
factors** — "app orders −95%, GrabFood outage, dinner daypart" — so the person who gets
the alert gets an *explanation*, not a spreadsheet. That's the analyst root-cause hunt,
done in seconds.

**It closes the loop back to security — where we started.** Because part of our team
comes from threat detection, we couldn't stop at sales. The platform also watches the
*stores themselves* — POS, network, payment gateways, and **password-intrusion
attempts** — and lets operations **remediate with one click**: restart the service, fail
over the network, block the intruding IP. The same detect-and-respond discipline that
protects enterprises, now protecting a restaurant's shift.

## Why us

Anyone can build a dashboard. This works because it was built by people who have
*felt the 4pm phone call* — who know that leadership wants the headline, finance wants
the variance explained, and operations wants the fix, not the autopsy. We paired lived
F&B and hospitality experience — and the standards of companies like **Starbucks,
Apple, GE, and Geico** — with a security team's obsession with fast detection and a
builder's discipline for shipping. And we did it for the Vietnamese market, from inside
it.

We built the product we needed in our old jobs. Now we're handing it to the people
still living the problem we left behind.
