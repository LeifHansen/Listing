import { useCallback, useEffect, useState } from "react";
import {
  CheckCircle2, ExternalLink, FileDown, Loader2, MapPin, Package,
  Printer, RefreshCw, Ship, Truck,
} from "lucide-react";
import { cn, formatMoney } from "@/lib/utils";
import { api, postJson } from "@/lib/api";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select } from "@/components/ui/fields";

/* ShippingDialog — sold → label, without leaving the app.

   Opens from a sold notification (one listing's order) or the Listings
   header (every order awaiting shipment). Two ways to get the label:

   - eBay: live eBay-negotiated rates via the Logistics API → buy → print.
     Limited-release on eBay's side, so when it isn't enabled for this app
     the option explains itself and Pirate Ship carries the workflow.
   - Pirate Ship: no public API exists, so this exports the order(s) as a
     CSV shaped for Pirate Ship's spreadsheet importer, links the seller
     there, and takes the tracking number back to mark the order shipped
     on eBay (which emails the buyer). */

const PIRATE_SHIP_URL = "https://ship.pirateship.com";

const emptyPkg = { weight_lb: "", weight_oz: "", length_in: "", width_in: "", height_in: "" };

function pkgFrom(order) {
  const p = order?.package || {};
  return {
    weight_lb: p.weight_lb || "", weight_oz: p.weight_oz || "",
    length_in: p.length_in || "", width_in: p.width_in || "",
    height_in: p.height_in || "",
  };
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

export function ShippingDialog() {
  const { shipping, closeShipping, ebay, loadNotifications } = useApp();
  const { toast } = useToast();
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
  const [pkg, setPkg] = useState(emptyPkg);
  const [method, setMethod] = useState("ebay");   // "ebay" | "pirateship"

  // eBay label path
  const [shipFrom, setShipFrom] = useState({
    name: "", address1: "", city: "", state: "", postal_code: "",
  });
  const [quote, setQuote] = useState(null);       // {shipping_quote_id, rates}
  const [rateId, setRateId] = useState("");
  const [quoting, setQuoting] = useState(false);
  const [buying, setBuying] = useState(false);
  const [label, setLabel] = useState(null);       // purchase result

  // Pirate Ship path
  const [tracking, setTracking] = useState("");
  const [carrier, setCarrier] = useState("USPS");
  const [marking, setMarking] = useState(false);
  const [shipped, setShipped] = useState(false);

  const reset = useCallback(() => {
    setOrders([]); setOrder(null); setPkg(emptyPkg); setQuote(null);
    setRateId(""); setLabel(null); setTracking(""); setShipped(false);
    setLoadError(""); setNotice("");
  }, []);

  const pickOrder = useCallback((o) => {
    setOrder(o);
    setPkg(pkgFrom(o));
    setQuote(null); setRateId(""); setLabel(null);
    setTracking(""); setShipped(false);
  }, []);

  /* Opening the dialog starts a fresh shipping session, so the previous one's
     order, package, quote and label have to be cleared and the default label
     path re-picked. That clearing happens DURING RENDER, tracking the previous
     session in state, which is React's documented way to reset state when an
     input changes (react.dev/learn/you-might-not-need-an-effect). Doing it in
     the effect below instead — as this used to — painted one frame of the
     *last* order's details (and its "Label purchased" panel) before the reset
     landed, and cost an extra render every time.

     A session is identified by: the dialog being open, which listing it was
     opened for, and whether eBay labels are available — the same three inputs
     the loading effect keys off, so a change to any of them re-runs both. */
  const sessionKey = open ? `${listingId}|${ebay.labels_enabled ? 1 : 0}` : null;
  const [prevSessionKey, setPrevSessionKey] = useState(null);
  if (sessionKey !== prevSessionKey) {
    setPrevSessionKey(sessionKey);
    if (open) {
      reset();
      setMethod(ebay.labels_enabled ? "ebay" : "pirateship");
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
          else setLoadError("No open order found for this listing — it may already be shipped.");
        } else {
          const res = await api("/api/ebay/orders");
          const list = res.orders || [];
          setOrders(list);
          if (list.length === 1) pickOrder(list[0]);
          else if (list.length === 0) setLoadError("No orders are waiting to ship. 🎉");
          // One page, not necessarily the pile. Saying so beats a seller
          // reading fifty as everything and leaving thirty unshipped — eBay
          // scores late dispatch.
          // Its own channel, not loadError: this is not a failure, and
          // overloading the error state with a notice is how the next bug
          // gets written.
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
  }, [open, listingId, ebay.labels_enabled, pickOrder]);

  const getRates = async () => {
    setQuoting(true);
    setQuote(null); setRateId("");
    try {
      const res = await postJson("/api/ebay/shipping-quote", {
        order_id: order.order_id,
        package: pkg,
        ship_from: shipFrom,
      });
      setQuote(res);
      if (res.rates?.length) setRateId(res.rates[0].rate_id);
      else toast("eBay returned no rates for that package.", { kind: "warning" });
    } catch (e) {
      toast(e.message, { kind: "error" });
    } finally {
      setQuoting(false);
    }
  };

  const buyLabel = async () => {
    setBuying(true);
    try {
      const res = await postJson("/api/ebay/shipping-label", {
        shipping_quote_id: quote.shipping_quote_id, rate_id: rateId,
      });
      setLabel(res);
      loadNotifications();
      toast("Label purchased — tracking was added to the order automatically.", { kind: "success" });
    } catch (e) {
      toast(e.message, { kind: "error" });
    } finally {
      setBuying(false);
    }
  };

  const markShipped = async () => {
    if (!tracking.trim()) { toast("Paste the tracking number first.", { kind: "warning" }); return; }
    setMarking(true);
    try {
      await postJson("/api/ebay/mark-shipped", {
        order_id: order.order_id, tracking_number: tracking, carrier,
      });
      setShipped(true);
      loadNotifications();
      toast("Order marked shipped — eBay is emailing the buyer the tracking.", { kind: "success" });
    } catch (e) {
      toast(e.message, { kind: "error" });
    } finally {
      setMarking(false);
    }
  };

  const csvHref = order
    ? `/api/shipping/pirateship.csv?order_id=${encodeURIComponent(order.order_id)}`
    : "/api/shipping/pirateship.csv";

  const itemTitle = order?.line_items?.map((li) => li.title).filter(Boolean).join("; ");

  return (
    <Dialog open={open} onClose={closeShipping} title="Ship a sold item" wide>
      {loading && (
        <p className="flex items-center gap-2 py-8 justify-center text-sm text-ink-secondary">
          <Loader2 size={16} className="animate-spin" aria-hidden /> Loading your orders…
        </p>
      )}

      {!loading && loadError && !order && (
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

          {/* Package — shared by both label paths. */}
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

          {/* Method tabs */}
          <div className="flex items-center gap-1.5">
            {[
              { id: "ebay", label: "eBay label", icon: Truck },
              { id: "pirateship", label: "Pirate Ship", icon: Ship },
            ].map((m) => (
              <button
                key={m.id}
                type="button"
                onClick={() => setMethod(m.id)}
                aria-pressed={method === m.id}
                className={cn(
                  "inline-flex items-center gap-1.5 h-9 px-3.5 rounded-full text-[13px]",
                  "font-semibold cursor-pointer transition-colors duration-150 border",
                  method === m.id
                    ? "bg-blue text-on-accent border-blue"
                    : "bg-card text-ink-secondary border-line hover:text-ink hover:border-line-strong",
                )}
              >
                <m.icon size={15} aria-hidden /> {m.label}
              </button>
            ))}
          </div>

          {/* --- eBay label path --- */}
          {method === "ebay" && (
            <div className="flex flex-col gap-4">
              {!ebay.labels_enabled && (
                <p className="text-[13px] text-ink-secondary rounded-input bg-warning-soft border border-warning/30 px-4 py-3">
                  eBay label purchasing isn't enabled for this app yet (eBay approves
                  its Logistics API per application). Use the Pirate Ship tab — same
                  discounted USPS/UPS rates, two extra clicks.
                </p>
              )}
              {label ? (
                <div className="rounded-input bg-green-soft border border-green/30 px-4 py-4 flex flex-col gap-2">
                  <p className="flex items-center gap-2 font-semibold text-sm text-ink">
                    <CheckCircle2 size={17} className="text-green" aria-hidden />
                    Label purchased{label.cost ? ` — ${formatMoney(label.cost, label.currency)}` : ""}
                  </p>
                  {label.tracking_number && (
                    <p className="text-[13px] text-ink-secondary">
                      Tracking: <span className="font-mono text-ink">{label.tracking_number}</span>
                      {" "}({label.carrier} {label.service}) — already on the order.
                    </p>
                  )}
                  <div className="flex flex-wrap gap-2 mt-1">
                    {(label.download_path || label.label_url) && (
                      <Button variant="primary" size="sm"
                        onClick={() => window.open(label.download_path || label.label_url, "_blank")}>
                        <Printer aria-hidden /> Open label PDF
                      </Button>
                    )}
                    <Button variant="ghost" size="sm" onClick={closeShipping}>Done</Button>
                  </div>
                </div>
              ) : (
                <>
                  {/* Ship-from — the Logistics API needs a full return address;
                      saved to prefs on first use so it's one-time. */}
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                    <Field label="Ship from — name" className="col-span-2">
                      <Input value={shipFrom.name} autoComplete="name"
                        onChange={(e) => setShipFrom({ ...shipFrom, name: e.target.value })} />
                    </Field>
                    <Field label="Street address" className="col-span-2">
                      <Input value={shipFrom.address1} autoComplete="street-address"
                        onChange={(e) => setShipFrom({ ...shipFrom, address1: e.target.value })} />
                    </Field>
                    <Field label="City" className="col-span-2 sm:col-span-2">
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
                  </div>

                  <div>
                    <Button variant="secondary" onClick={getRates} loading={quoting}
                      disabled={!ebay.labels_enabled}>
                      <RefreshCw aria-hidden /> Get eBay rates
                    </Button>
                  </div>

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
                                {r.delivery_est && (
                                  <span className="block text-[12px] text-ink-secondary">
                                    Est. delivery {new Date(r.delivery_est).toLocaleDateString()}
                                  </span>
                                )}
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
                          <Printer aria-hidden /> Buy label
                          {rateId && quote.rates.find((r) => r.rate_id === rateId)
                            ? ` — ${formatMoney(quote.rates.find((r) => r.rate_id === rateId).cost,
                              quote.rates.find((r) => r.rate_id === rateId).currency)}`
                            : ""}
                        </Button>
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {/* --- Pirate Ship path --- */}
          {method === "pirateship" && (
            <div className="flex flex-col gap-4">
              {shipped ? (
                <div className="rounded-input bg-green-soft border border-green/30 px-4 py-4 flex flex-col gap-2">
                  <p className="flex items-center gap-2 font-semibold text-sm text-ink">
                    <CheckCircle2 size={17} className="text-green" aria-hidden />
                    Marked shipped — eBay is emailing the buyer the tracking number.
                  </p>
                  <div><Button variant="ghost" size="sm" onClick={closeShipping}>Done</Button></div>
                </div>
              ) : (
                <>
                  <ol className="flex flex-col gap-2 text-[13px] text-ink-secondary list-decimal pl-5 leading-relaxed">
                    <li>
                      Download this order as a CSV (buyer address, weight, and
                      dimensions ride along).
                    </li>
                    <li>
                      In Pirate Ship, choose <strong className="text-ink">Ship → Upload a
                      Spreadsheet</strong> and drop the file in — their importer maps the
                      columns automatically. Buy the label there (their discounted
                      USPS/UPS rates).
                    </li>
                    <li>
                      Paste the tracking number back here — that marks the order
                      shipped on eBay and emails the buyer.
                    </li>
                  </ol>
                  <div className="flex flex-wrap gap-2">
                    <Button variant="secondary" size="sm"
                      onClick={() => { window.location.href = csvHref; }}>
                      <FileDown aria-hidden /> Download CSV
                    </Button>
                    <Button variant="secondary" size="sm"
                      onClick={() => window.open(PIRATE_SHIP_URL, "_blank", "noopener")}>
                      <ExternalLink aria-hidden /> Open Pirate Ship
                    </Button>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-[1fr_10rem_auto] gap-3 items-end">
                    <Field label="Tracking number">
                      <Input value={tracking} placeholder="9400 1000 0000 …"
                        onChange={(e) => setTracking(e.target.value)} />
                    </Field>
                    <Field label="Carrier">
                      <Select value={carrier} onChange={(e) => setCarrier(e.target.value)}>
                        <option value="USPS">USPS</option>
                        <option value="UPS">UPS</option>
                        <option value="FEDEX">FedEx</option>
                      </Select>
                    </Field>
                    <Button variant="primary" onClick={markShipped} loading={marking}>
                      <CheckCircle2 aria-hidden /> Mark shipped
                    </Button>
                  </div>
                </>
              )}
            </div>
          )}

          {/* Back to the pick list in multi-order mode. */}
          {orders.length > 1 && (
            <button
              type="button"
              onClick={() => setOrder(null)}
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
