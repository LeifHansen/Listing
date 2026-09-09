import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle, CheckCircle2, ExternalLink, Link2, Loader2, MapPin, Package,
  Printer, RefreshCw, Truck,
} from "lucide-react";
import { formatMoney } from "@/lib/utils";
import { api, postJson } from "@/lib/api";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/fields";
import { TagPill } from "@/components/ui/badges";

/* ShippingDialog — sold → label, without leaving the app.

   Opens from a sold notification (one listing's order) or the Listings
   header (every order awaiting shipment). Labels are bought through the
   seller's OWN EasyPost account — connected once in Settings, their wallet
   paying for postage — so the one flow here is: package → ship-from → live
   rates → buy → print, with the tracking number posted to the eBay order
   automatically (which emails the buyer).

   Every purchase is recorded server-side, so an order that already has a
   label opens on that label rather than offering to buy a second one, and a
   purchase whose answer was lost is settled against EasyPost before anything
   is bought again. */

const emptyPkg = { weight_lb: "", weight_oz: "", length_in: "", width_in: "", height_in: "" };
const emptyShipFrom = {
  name: "", address1: "", address2: "", city: "", state: "", postal_code: "",
  country: "US", phone: "",
};

function pkgFrom(order) {
  const p = order?.package || {};
  return {
    weight_lb: p.weight_lb || "", weight_oz: p.weight_oz || "",
    length_in: p.length_in || "", width_in: p.width_in || "",
    height_in: p.height_in || "",
  };
}

const boughtLabel = (order) => (order?.labels || []).find((l) => l.status === "bought") || null;
const pendingLabel = (order) => (order?.labels || []).find((l) => l.status === "buying") || null;

/* What an empty awaiting-shipment list means. The server's answer carries
   eBay's own count of every order in the window, the account it looked at
   and the environment, because "No orders are waiting to ship" said the
   same thing whether every order was already shipped, a different account
   had sold, or the server was on eBay's sandbox — the three things a seller
   who KNOWS something sold needs told apart. Exported for its test. */
export function emptyPileCopy({ recent_total, ebay_username, env } = {}) {
  const who = ebay_username ? ` on ${ebay_username}` : "";
  let text;
  if (recent_total === 0) {
    text = `eBay has no orders at all in the last 90 days${who}. If the sale was on a `
      + "different eBay account, connect that one in Settings.";
  } else if (recent_total > 0) {
    text = `No orders are waiting to ship — eBay shows ${recent_total} order`
      + `${recent_total === 1 ? "" : "s"} in the last 90 days${who}, all already `
      + "shipped or marked shipped.";
  } else {
    text = `No orders are waiting to ship${who}.`;
  }
  if (env && env !== "production") {
    text += " This app is connected to the eBay sandbox, which has no real orders — "
      + "the server needs EBAY_ENV=production.";
  }
  return text;
}

function AddressBlock({ order }) {
  const to = order.ship_to || {};
  return (
    <div className="rounded-input bg-bg-sunken border border-line px-4 py-3 text-[13px] leading-relaxed">
      <p className="flex items-center gap-1.5 font-semibold text-ink">
        <MapPin size={14} className="text-blue shrink-0" aria-hidden />
        {to.name || "Buyer"}
        {order.buyer_username && (
          <span className="font-normal text-ink-faint">({order.buyer_username})</span>
        )}
      </p>
      <p className="text-ink-secondary">
        {[to.address1, to.address2].filter(Boolean).join(", ")}<br />
        {to.city}, {to.state} {to.postal_code} {to.country !== "US" ? to.country : ""}
      </p>
    </div>
  );
}

/* The bought label: tracking, the PDF, and where eBay stands. The PDF opens
   from EasyPost's own URL rather than through this server, because a
   same-origin navigation is exactly what the native shell cannot
   authenticate — one URL works on the web and in the app alike. */
function LabelPanel({ label, onRetryEbay, marking, onVoid, voiding, onDone }) {
  const voided = label.status === "refund_requested";
  return (
    <div className="rounded-input bg-green-soft border border-green/30 px-4 py-4 flex flex-col gap-2">
      <p className="flex items-center gap-2 font-semibold text-sm text-ink">
        <CheckCircle2 size={17} className="text-green" aria-hidden />
        {voided ? "Label voided — refund requested" : "Label purchased"}
        {label.cost ? ` — ${formatMoney(label.cost, label.currency || "USD")}` : ""}
      </p>
      {label.tracking_number && (
        <p className="text-[13px] text-ink-secondary">
          Tracking: <span className="font-mono text-ink">{label.tracking_number}</span>
          {label.carrier ? ` (${label.carrier} ${label.service || ""})` : ""}
        </p>
      )}
      {label.ebay_marked ? (
        <p className="text-[13px] text-ink-secondary">
          Tracking added to the eBay order — eBay is emailing the buyer.
        </p>
      ) : (
        <div className="rounded-input bg-warning-soft border border-warning/30 px-3 py-2 text-[13px] text-ink-secondary flex flex-col gap-2">
          <p className="flex items-start gap-2">
            <AlertTriangle size={15} className="text-warning shrink-0 mt-0.5" aria-hidden />
            <span>
              Label bought, but eBay couldn't be updated
              {label.ebay_error ? `: ${label.ebay_error}` : "."}
              {label.ebay_outcome_unknown
                ? " Check the order on eBay before retrying — it may already show shipped."
                : ""}
            </span>
          </p>
          <div>
            <Button variant="secondary" size="sm" onClick={onRetryEbay} loading={marking}>
              <RefreshCw aria-hidden /> Retry marking shipped on eBay
            </Button>
          </div>
        </div>
      )}
      <div className="flex flex-wrap gap-2 mt-1">
        {label.label_url && (
          <Button variant="primary" size="sm"
            onClick={() => window.open(label.label_url, "_blank", "noopener")}>
            <Printer aria-hidden /> Open label PDF
          </Button>
        )}
        {label.tracker_url && (
          <Button variant="secondary" size="sm"
            onClick={() => window.open(label.tracker_url, "_blank", "noopener")}>
            <ExternalLink aria-hidden /> Track package
          </Button>
        )}
        {!voided && (
          <Button variant="ghost" size="sm" onClick={onVoid} loading={voiding}>
            Void label
          </Button>
        )}
        <Button variant="ghost" size="sm" onClick={onDone}>Done</Button>
      </div>
    </div>
  );
}

export function ShippingDialog() {
  const {
    shipping, closeShipping, easypost, loadNotifications, setView,
  } = useApp();
  const { toast, confirm } = useToast();
  const open = shipping != null;
  const listingId = shipping?.listingId || null;

  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  // One page is not necessarily the pile. Saying so beats a seller
  // reading fifty orders as everything and leaving thirty unshipped —
  // eBay scores late dispatch.
  const [notice, setNotice] = useState("");
  const [orders, setOrders] = useState([]);       // generic mode: the pick list
  const [order, setOrder] = useState(null);       // the order being shipped
  const [pastLabel, setPastLabel] = useState(null); // for-listing mode: already shipped
  const [pkg, setPkg] = useState(emptyPkg);

  const [shipFrom, setShipFrom] = useState(emptyShipFrom);
  const [quote, setQuote] = useState(null);       // {shipment_id, rates, messages, test}
  const [rateId, setRateId] = useState("");
  const [quoting, setQuoting] = useState(false);
  const [buying, setBuying] = useState(false);
  const [label, setLabel] = useState(null);       // the bought label
  const [checking, setChecking] = useState(false); // settling a lost purchase
  const [marking, setMarking] = useState(false);
  const [voiding, setVoiding] = useState(false);

  const reset = useCallback(() => {
    setOrders([]); setOrder(null); setPastLabel(null); setPkg(emptyPkg);
    setQuote(null); setRateId(""); setLabel(null); setChecking(false);
    setLoadError(""); setNotice("");
  }, []);

  /* A purchase whose answer never came back leaves a "buying" label on the
     order. Ask the server to settle it against EasyPost before offering to
     buy again — that is what stops one lost answer becoming two charges. */
  const settlePending = useCallback(async (o) => {
    setChecking(true);
    try {
      const res = await postJson("/api/easypost/label", { order_id: o.order_id });
      if (res.status === "bought") {
        setLabel(res);
        toast("An earlier purchase had gone through — here is that label.", { kind: "success" });
      }
    } catch (e) {
      toast(e.message, { kind: "error" });
    } finally {
      setChecking(false);
    }
  }, [toast]);

  const pickOrder = useCallback((o) => {
    setOrder(o);
    setPkg(pkgFrom(o));
    setQuote(null); setRateId("");
    // An order that already has a label opens ON that label — buying a
    // second one is the mistake this whole surface exists to prevent.
    setLabel(boughtLabel(o));
    if (!boughtLabel(o) && pendingLabel(o)) settlePending(o);
  }, [settlePending]);

  /* Opening the dialog starts a fresh shipping session, so the previous one's
     order, package, quote and label have to be cleared. That clearing happens
     DURING RENDER, tracking the previous session in state, which is React's
     documented way to reset state when an input changes
     (react.dev/learn/you-might-not-need-an-effect). Doing it in the effect
     below instead painted one frame of the *last* order's details before the
     reset landed, and cost an extra render every time.

     A session is identified by: the dialog being open, which listing it was
     opened for, and whether EasyPost is connected — the same three inputs
     the loading effect keys off, so a change to any of them re-runs both. */
  const sessionKey = open ? `${listingId}|${easypost.connected ? 1 : 0}` : null;
  const [prevSessionKey, setPrevSessionKey] = useState(null);
  if (sessionKey !== prevSessionKey) {
    setPrevSessionKey(sessionKey);
    if (open) {
      reset();
      setLoading(true);
    }
  }

  // Load the order(s) for the session opened above; remember the saved ship-from.
  useEffect(() => {
    if (!open) return;
    (async () => {
      try {
        if (listingId) {
          const res = await api(`/api/ebay/orders/for-listing/${listingId}`);
          if (res.order) pickOrder(res.order);
          else {
            const done = (res.labels || []).find((l) => l.status === "bought");
            if (done) setPastLabel(done);
            else setLoadError("No open order found for this listing — it may already be shipped.");
          }
        } else {
          const res = await api("/api/ebay/orders");
          const list = res.orders || [];
          setOrders(list);
          if (list.length === 1) pickOrder(list[0]);
          else if (list.length === 0) setLoadError(emptyPileCopy(res));
          // One page, not necessarily the pile. Its own channel, not
          // loadError: this is not a failure, and overloading the error
          // state with a notice is how the next bug gets written.
          else if (res.partial) {
            setNotice(`Showing the first ${list.length} of ${res.total} orders `
              + "waiting to ship — ship these and reopen this for the rest.");
          }
        }
      } catch (e) {
        setLoadError(e.message);
      } finally {
        setLoading(false);
      }
      try {
        const p = await api("/api/prefs");
        const saved = p.prefs?.ship_from;
        if (saved) setShipFrom((f) => ({ ...f, ...saved }));
      } catch (e) { /* logged out or no prefs — the form stays blank */ }
    })();
  }, [open, listingId, easypost.connected, pickOrder]);

  const getRates = async () => {
    setQuoting(true);
    setQuote(null); setRateId("");
    try {
      const res = await postJson("/api/easypost/rates", {
        order_id: order.order_id,
        package: pkg,
        ship_from: shipFrom,
      });
      setQuote(res);
      if (res.rates?.length) setRateId(res.rates[0].rate_id);
      else if (!res.messages?.length) toast("EasyPost returned no rates for that package.", { kind: "warning" });
    } catch (e) {
      toast(e.message, { kind: "error" });
    } finally {
      setQuoting(false);
    }
  };

  const buyLabel = async () => {
    setBuying(true);
    try {
      const res = await postJson("/api/easypost/label", {
        order_id: order.order_id,
        shipment_id: quote.shipment_id,
        rate_id: rateId,
        listing_record_id: order.listing_record_id || "",
      });
      setLabel(res);
      loadNotifications();
      if (res.ebay_marked) {
        toast("Label purchased — tracking was added to the eBay order.", { kind: "success" });
      } else {
        toast("Label purchased, but eBay couldn't be updated — retry from the panel.", { kind: "warning" });
      }
    } catch (e) {
      toast(e.message, { kind: "error" });
    } finally {
      setBuying(false);
    }
  };

  const retryEbay = async () => {
    setMarking(true);
    try {
      await postJson("/api/ebay/mark-shipped", {
        order_id: label.order_id || order?.order_id,
        tracking_number: label.tracking_number,
        carrier: label.ebay_carrier || label.carrier || "USPS",
      });
      setLabel({ ...label, ebay_marked: true, ebay_error: "", ebay_outcome_unknown: false });
      loadNotifications();
      toast("Order marked shipped — eBay is emailing the buyer the tracking.", { kind: "success" });
    } catch (e) {
      toast(e.message, { kind: "error" });
    } finally {
      setMarking(false);
    }
  };

  const voidLabel = async () => {
    if (!(await confirm({
      title: "Void this label?",
      message: "EasyPost asks the carrier to refund the postage. Only void a label you "
        + "haven't used — the tracking number stays on the eBay order until you "
        + "change it in Seller Hub.",
      confirmLabel: "Void label",
      danger: true,
    }))) return;
    setVoiding(true);
    try {
      await postJson(`/api/easypost/label/${encodeURIComponent(label.shipment_id)}/refund`, {});
      setLabel({ ...label, status: "refund_requested" });
      toast("Void requested — EasyPost confirms the refund within a few days.", { kind: "success" });
    } catch (e) {
      toast(e.message, { kind: "error" });
    } finally {
      setVoiding(false);
    }
  };

  const itemTitle = order?.line_items?.map((li) => li.title).filter(Boolean).join("; ");
  const chosen = quote?.rates?.find((r) => r.rate_id === rateId);
  const testMode = !!(quote?.test ?? easypost.test);
  const shownLabel = label || pastLabel;

  return (
    <Dialog open={open} onClose={closeShipping} title="Ship a sold item" wide>
      {loading && (
        <p className="flex items-center gap-2 py-8 justify-center text-sm text-ink-secondary">
          <Loader2 size={16} className="animate-spin" aria-hidden /> Loading your orders…
        </p>
      )}

      {!loading && loadError && !order && !pastLabel && (
        <p className="py-6 text-center text-sm text-ink-secondary">{loadError}</p>
      )}

      {!loading && notice && !order && (
        <p className="pb-3 text-[13px] text-ink-secondary">{notice}</p>
      )}

      {/* Generic mode: several awaiting orders — pick one. */}
      {!loading && !order && orders.length > 1 && (
        <ul className="divide-y divide-line border border-line rounded-input overflow-hidden">
          {orders.map((o) => (
            <li key={o.order_id}>
              <button
                type="button"
                onClick={() => pickOrder(o)}
                className="w-full flex items-center gap-3 px-4 py-3 text-left cursor-pointer hover:bg-bg-sunken"
              >
                <Package size={17} className="text-blue shrink-0" aria-hidden />
                <span className="min-w-0 flex-1">
                  <span className="block font-semibold text-[13px] text-ink truncate">
                    {o.line_items?.map((li) => li.title).join("; ") || o.order_id}
                  </span>
                  <span className="block text-[12px] text-ink-secondary">
                    {o.ship_to?.name} · {o.ship_to?.city}, {o.ship_to?.state}
                    {boughtLabel(o) ? " · label bought" : ""}
                  </span>
                </span>
                <span className="font-semibold text-sm text-ink tabular-nums shrink-0">
                  {formatMoney(o.total, o.currency || "USD")}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {/* For-listing mode after the order has shipped: the label it went with. */}
      {!loading && !order && pastLabel && (
        <div className="flex flex-col gap-3">
          <p className="text-sm text-ink-secondary">
            This order has already shipped — here is the label it went with.
          </p>
          <LabelPanel label={pastLabel} onRetryEbay={retryEbay} marking={marking}
            onVoid={() => { setLabel(pastLabel); voidLabel(); }} voiding={voiding}
            onDone={closeShipping} />
        </div>
      )}

      {order && (
        <div className="flex flex-col gap-4">
          {/* What & where */}
          <div className="flex flex-col gap-2">
            <p className="text-sm font-semibold text-ink truncate">
              {itemTitle || `Order ${order.order_id}`}
              {order.total && (
                <span className="ml-2 font-normal text-ink-secondary">
                  {formatMoney(order.total, order.currency || "USD")}
                </span>
              )}
            </p>
            <AddressBlock order={order} />
          </div>

          {shownLabel ? (
            <LabelPanel label={shownLabel} onRetryEbay={retryEbay} marking={marking}
              onVoid={voidLabel} voiding={voiding} onDone={closeShipping} />
          ) : checking ? (
            <p className="flex items-center gap-2 text-sm text-ink-secondary">
              <Loader2 size={16} className="animate-spin" aria-hidden />
              We may already have bought this label — checking with EasyPost…
            </p>
          ) : !easypost.loaded ? (
            <p className="flex items-center gap-2 text-sm text-ink-secondary">
              <Loader2 size={16} className="animate-spin" aria-hidden />
              Checking your EasyPost connection…
            </p>
          ) : !easypost.connected ? (
            <div className="rounded-input bg-warning-soft border border-warning/30 px-4 py-4 flex flex-col gap-3">
              <p className="text-[13px] text-ink-secondary">
                Labels are bought through your own EasyPost account — discounted USPS
                and UPS rates, with the postage billed to you. Connect it once in
                Settings and every sale ships from here.
              </p>
              <div>
                <Button variant="primary" size="sm"
                  onClick={() => { closeShipping(); setView("settings"); }}>
                  <Link2 aria-hidden /> Connect EasyPost
                </Button>
              </div>
            </div>
          ) : (
            <>
              {/* Package */}
              <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
                <Field label="Weight (lb)">
                  <Input type="number" min="0" inputMode="decimal" value={pkg.weight_lb}
                    onChange={(e) => setPkg({ ...pkg, weight_lb: e.target.value })} />
                </Field>
                <Field label="Weight (oz)">
                  <Input type="number" min="0" inputMode="decimal" value={pkg.weight_oz}
                    onChange={(e) => setPkg({ ...pkg, weight_oz: e.target.value })} />
                </Field>
                <Field label="L (in)" help="Optional — needed for accurate rates on larger boxes">
                  <Input type="number" min="0" inputMode="decimal" value={pkg.length_in}
                    onChange={(e) => setPkg({ ...pkg, length_in: e.target.value })} />
                </Field>
                <Field label="W (in)">
                  <Input type="number" min="0" inputMode="decimal" value={pkg.width_in}
                    onChange={(e) => setPkg({ ...pkg, width_in: e.target.value })} />
                </Field>
                <Field label="H (in)">
                  <Input type="number" min="0" inputMode="decimal" value={pkg.height_in}
                    onChange={(e) => setPkg({ ...pkg, height_in: e.target.value })} />
                </Field>
              </div>

              {/* Ship-from — remembered in prefs on first use so it's one-time. */}
              <div className="grid grid-cols-2 sm:grid-cols-6 gap-3">
                <Field label="Ship from — name" className="col-span-2">
                  <Input value={shipFrom.name} autoComplete="name"
                    onChange={(e) => setShipFrom({ ...shipFrom, name: e.target.value })} />
                </Field>
                <Field label="Street address" className="col-span-2">
                  <Input value={shipFrom.address1} autoComplete="street-address"
                    onChange={(e) => setShipFrom({ ...shipFrom, address1: e.target.value })} />
                </Field>
                <Field label="Apt / suite" className="col-span-2">
                  <Input value={shipFrom.address2}
                    onChange={(e) => setShipFrom({ ...shipFrom, address2: e.target.value })} />
                </Field>
                <Field label="City" className="col-span-2">
                  <Input value={shipFrom.city}
                    onChange={(e) => setShipFrom({ ...shipFrom, city: e.target.value })} />
                </Field>
                <Field label="State">
                  <Input value={shipFrom.state} maxLength={2} placeholder="CA"
                    onChange={(e) => setShipFrom({ ...shipFrom, state: e.target.value.toUpperCase() })} />
                </Field>
                <Field label="ZIP">
                  <Input value={shipFrom.postal_code} inputMode="numeric"
                    onChange={(e) => setShipFrom({ ...shipFrom, postal_code: e.target.value })} />
                </Field>
                <Field label="Phone" help="UPS and FedEx rates need one" className="col-span-2">
                  <Input value={shipFrom.phone} inputMode="tel" autoComplete="tel"
                    onChange={(e) => setShipFrom({ ...shipFrom, phone: e.target.value })} />
                </Field>
                <Field label="Country">
                  <Input value={shipFrom.country} maxLength={2} placeholder="US"
                    onChange={(e) => setShipFrom({ ...shipFrom, country: e.target.value.toUpperCase() })} />
                </Field>
              </div>

              <div className="flex flex-wrap items-center gap-3">
                <Button variant="secondary" onClick={getRates} loading={quoting}>
                  <RefreshCw aria-hidden /> Get rates
                </Button>
                {testMode && (
                  <TagPill tone="yellow" title="EasyPost test keys buy free sample labels that carriers won't accept">
                    Test mode — sample labels
                  </TagPill>
                )}
              </div>

              {quote && !quote.rates?.length && quote.messages?.length > 0 && (
                <div className="rounded-input bg-warning-soft border border-warning/30 px-4 py-3 text-[13px] text-ink-secondary">
                  <p className="font-semibold text-ink mb-1">No rates for that package:</p>
                  <ul className="list-disc pl-5">
                    {quote.messages.map((m, i) => (
                      <li key={i}>{m.carrier ? `${m.carrier}: ` : ""}{m.message}</li>
                    ))}
                  </ul>
                </div>
              )}

              {quote?.rates?.length > 0 && (
                <div className="flex flex-col gap-2">
                  <ul className="divide-y divide-line border border-line rounded-input overflow-hidden">
                    {quote.rates.map((r) => (
                      <li key={r.rate_id}>
                        <label className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-bg-sunken">
                          <input type="radio" name="ship-rate" className="accent-[var(--brand-blue)]"
                            checked={rateId === r.rate_id}
                            onChange={() => setRateId(r.rate_id)} />
                          <span className="min-w-0 flex-1">
                            <span className="block font-semibold text-[13px] text-ink">
                              {r.carrier} {r.service}
                            </span>
                            {r.delivery_days ? (
                              <span className="block text-[12px] text-ink-secondary">
                                About {r.delivery_days} day{r.delivery_days === 1 ? "" : "s"}
                              </span>
                            ) : null}
                          </span>
                          <span className="font-bold text-sm text-ink tabular-nums shrink-0">
                            {formatMoney(r.cost, r.currency)}
                          </span>
                        </label>
                      </li>
                    ))}
                  </ul>
                  <div>
                    <Button variant="primary" onClick={buyLabel} loading={buying}
                      disabled={!rateId}>
                      <Truck aria-hidden /> Buy label
                      {chosen ? ` — ${formatMoney(chosen.cost, chosen.currency)}` : ""}
                    </Button>
                  </div>
                </div>
              )}
            </>
          )}

          {/* Back to the pick list in multi-order mode. */}
          {orders.length > 1 && (
            <button
              type="button"
              onClick={() => { setOrder(null); setLabel(null); }}
              className="self-start text-[13px] font-semibold text-blue cursor-pointer hover:underline"
            >
              ← All orders awaiting shipment
            </button>
          )}
        </div>
      )}
    </Dialog>
  );
}
