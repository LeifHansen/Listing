import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  MessagesSquare, ArrowUp, ArrowLeft, Plus, Search, Loader2, Send,
  MessageCircle, Trash2, Pencil, X, Tag,
} from "lucide-react";
import { api, postJson } from "@/lib/api";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { cn, formatMoney, once, timeAgo } from "@/lib/utils";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select, Textarea } from "@/components/ui/fields";
import { TagPill } from "@/components/ui/badges";
import { EmptyState } from "@/components/ui/EmptyState";
import { SearchIllustration } from "@/components/ui/illustrations";

// The community board. Threads, replies, and one upvote per person per item,
// all served by /api/forum — no AI, no tokens, nothing to connect first.
//
// Two screens live in here: the board (list + composer) and one thread. They
// share enough state (the signed-in user, the category catalog, the vote
// handler) that splitting them across files would mean threading half a dozen
// props through a router for no gain.

const SORTS = [
  { id: "recent", label: "Active" },
  { id: "new", label: "Newest" },
  { id: "top", label: "Top" },
  { id: "unanswered", label: "Unanswered" },
];

const PAGE_SIZE = 25;

// --- shared pieces ----------------------------------------------------------

function Avatar({ name, className }) {
  return (
    <span
      aria-hidden
      className={cn(
        "grid place-items-center size-9 shrink-0 rounded-full",
        "bg-blue-soft text-blue font-bold text-[13px] uppercase",
        className,
      )}
    >
      {(name || "?").slice(0, 2)}
    </span>
  );
}

// The upvote control: a pill that fills in once you've voted. Optimistic —
// the count moves under your thumb and rolls back if the server disagrees.
function VoteButton({ votes, voted, onVote, busy, label }) {
  return (
    <button
      type="button"
      onClick={onVote}
      disabled={busy}
      aria-pressed={voted}
      aria-label={`${voted ? "Remove your upvote from" : "Upvote"} ${label}`}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 h-8 shrink-0",
        "text-[13px] font-bold transition-colors duration-150 cursor-pointer",
        "disabled:opacity-50 disabled:cursor-default",
        voted
          ? "bg-blue text-on-accent"
          : "bg-bg-sunken text-ink-secondary hover:text-blue hover:bg-blue-soft",
      )}
    >
      <ArrowUp size={14} strokeWidth={2.5} aria-hidden />
      {votes}
    </button>
  );
}

// The listing a price-check post is about, as it looked when the post was
// written (the server snapshots it — see db.ForumPost.attachment).
function AttachmentCard({ attachment, onOpen }) {
  // Photos get offloaded to R2, purged, or replaced long after a thread is
  // written, so the snapshot's image URL is the one part of it that can rot.
  // Fall back to the tag glyph rather than a broken-image box.
  const [imgOk, setImgOk] = useState(true);
  if (!attachment) return null;
  const price = formatMoney(attachment.price, attachment.currency || "USD");
  return (
    <button
      type="button"
      onClick={onOpen}
      disabled={!onOpen}
      className={cn(
        "mt-3 w-full flex items-center gap-3 rounded-card border border-line",
        "bg-bg-sunken p-3 text-left transition-colors duration-150",
        onOpen ? "hover:border-line-strong cursor-pointer" : "cursor-default",
      )}
    >
      {attachment.image && imgOk ? (
        <img
          src={attachment.image}
          alt=""
          loading="lazy"
          onError={() => setImgOk(false)}
          className="size-14 rounded-[12px] object-cover bg-card shrink-0"
        />
      ) : (
        <span className="grid place-items-center size-14 rounded-[12px] bg-card text-ink-faint shrink-0">
          <Tag size={18} aria-hidden />
        </span>
      )}
      <span className="min-w-0 flex-1">
        <span className="block text-[13px] font-semibold text-ink truncate">
          {attachment.title || "Untitled listing"}
        </span>
        <span className="block text-xs text-ink-secondary">
          {price ? `Listed at ${price}` : "No price set"} · when this was posted
        </span>
      </span>
    </button>
  );
}

function Byline({ author, at, edited, category, categoryLabel }) {
  return (
    <div className="flex items-center gap-2 flex-wrap text-[13px] text-ink-secondary min-w-0">
      <span className="font-semibold text-ink truncate">{author}</span>
      <span aria-hidden>·</span>
      <time dateTime={at || undefined} title={at ? new Date(at).toLocaleString() : undefined}>
        {timeAgo(at)}
      </time>
      {edited && <span className="text-ink-faint">· edited</span>}
      {category && <TagPill tone="blue">{categoryLabel || category}</TagPill>}
    </div>
  );
}

// Author-only controls. Rendered nowhere else, so a reader never sees a
// button the server would refuse.
function OwnerActions({ onEdit, onDelete, busy, what }) {
  return (
    <div className="flex items-center gap-1 shrink-0">
      <Button variant="ghost" size="iconSm" onClick={onEdit}
              aria-label={`Edit your ${what}`} title="Edit">
        <Pencil size={15} aria-hidden />
      </Button>
      <Button variant="ghost" size="iconSm" onClick={onDelete} loading={busy}
              aria-label={`Delete your ${what}`} title="Delete"
              className="hover:text-error">
        <Trash2 size={15} aria-hidden />
      </Button>
    </div>
  );
}

// --- composer ---------------------------------------------------------------

function Composer({ categories, limits, drafts, onCancel, onPosted }) {
  const { listingsState } = useApp();
  const { toast } = useToast();
  const [category, setCategory] = useState(drafts?.category || "general");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [listingId, setListingId] = useState("");
  const [posting, setPosting] = useState(false);
  const titleRef = useRef(null);

  useEffect(() => { titleRef.current?.focus(); }, []);

  // Only the seller's own listings can ride along (the server enforces it
  // too) — attaching is for "here's mine, what would you price it at?".
  const attachable = useMemo(
    () => (listingsState.items || []).filter((i) => i.title).slice(0, 100),
    [listingsState.items],
  );

  const submit = once("forum-post", async () => {
    setPosting(true);
    try {
      const res = await postJson("/api/forum/posts", {
        category, title, body, listing_id: listingId,
      });
      setTitle(""); setBody(""); setListingId("");
      onPosted(res.post);
      toast("Posted to the community.", { kind: "success" });
    } catch (e) {
      toast(e.message, { kind: "error" });
    } finally {
      setPosting(false);
    }
  });

  return (
    <Card className="mb-4">
      <div className="flex items-center justify-between gap-3 mb-4">
        <h2 className="text-[17px] font-bold text-ink">Start a thread</h2>
        <Button variant="ghost" size="iconSm" onClick={onCancel} aria-label="Close">
          <X size={16} aria-hidden />
        </Button>
      </div>
      <div className="flex flex-col gap-3">
        <div className="grid sm:grid-cols-[200px_1fr] gap-3">
          <Field label="Section">
            <Select value={category} onChange={(e) => setCategory(e.target.value)}>
              {categories.map((c) => (
                <option key={c.id} value={c.id}>{c.label}</option>
              ))}
            </Select>
          </Field>
          <Field label="Title" hint={`${title.length}/${limits.title_max || 140}`}>
            <Input
              ref={titleRef}
              value={title}
              maxLength={limits.title_max || 140}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Ask it the way you'd ask the room"
            />
          </Field>
        </div>
        <Field label="Post">
          <Textarea
            rows={5}
            value={body}
            maxLength={limits.body_max || 8000}
            onChange={(e) => setBody(e.target.value)}
            placeholder="What did you find, what did it cost, and what are you trying to work out?"
          />
        </Field>
        {attachable.length > 0 && (
          <Field
            label="Attach one of your listings"
            hint="optional"
            help="Posts a snapshot — title, price, and one photo as they are right now. It won't change later when you edit or sell the item."
          >
            <Select value={listingId} onChange={(e) => setListingId(e.target.value)}>
              <option value="">No listing</option>
              {attachable.map((i) => (
                <option key={i.id} value={i.id}>{i.title}</option>
              ))}
            </Select>
          </Field>
        )}
        <div className="flex items-center justify-end gap-2 pt-1">
          <Button variant="ghost" onClick={onCancel}>Cancel</Button>
          <Button variant="primary" onClick={submit} loading={posting}>
            <Send size={16} aria-hidden /> Post
          </Button>
        </div>
      </div>
    </Card>
  );
}

// A small in-place editor, shared by posts and replies.
function InlineEditor({ initial, max, rows = 4, onCancel, onSave, saving }) {
  const [text, setText] = useState(initial || "");
  return (
    <div className="flex flex-col gap-2 mt-2">
      <Textarea rows={rows} value={text} maxLength={max}
                onChange={(e) => setText(e.target.value)} />
      <div className="flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
        <Button variant="primary" size="sm" loading={saving}
                onClick={() => onSave(text)}>Save</Button>
      </div>
    </div>
  );
}

// --- the board --------------------------------------------------------------

function PostRow({ post, categoryLabel, onOpen, onVote, voting }) {
  return (
    <motion.article
      layout="position"
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      className="flex gap-3.5 p-4 border-b border-line last:border-0"
    >
      <Avatar name={post.author} />
      <div className="min-w-0 flex-1">
        <button
          type="button"
          onClick={onOpen}
          className="block w-full text-left cursor-pointer group"
        >
          <h3 className="font-bold text-[15px] text-ink leading-snug group-hover:text-blue transition-colors">
            {post.title}
          </h3>
          <p className="mt-0.5 text-[13px] text-ink-secondary line-clamp-2 whitespace-pre-wrap">
            {post.body}
          </p>
        </button>
        <div className="mt-2 flex items-center gap-2 flex-wrap">
          <Byline author={post.author} at={post.last_activity_at}
                  category={post.category} categoryLabel={categoryLabel} />
          <button
            type="button"
            onClick={onOpen}
            className="inline-flex items-center gap-1 text-[13px] font-semibold text-ink-secondary hover:text-blue cursor-pointer"
          >
            <MessageCircle size={14} aria-hidden />
            {post.replies}
          </button>
        </div>
      </div>
      <VoteButton votes={post.votes} voted={post.voted} onVote={onVote}
                  busy={voting} label={post.title} />
    </motion.article>
  );
}

// --- one thread -------------------------------------------------------------

function Thread({ postId, categories, limits, onBack, onChanged }) {
  const { user, openAuth, openListing } = useApp();
  const { toast } = useToast();
  const [state, setState] = useState({ loading: true, post: null, replies: [] });
  const [reply, setReply] = useState("");
  const [posting, setPosting] = useState(false);
  const [editing, setEditing] = useState(null); // null | "post" | reply id
  const [saving, setSaving] = useState(false);
  const [busyVote, setBusyVote] = useState("");

  const label = useCallback(
    (id) => categories.find((c) => c.id === id)?.label || id,
    [categories],
  );

  const load = useCallback(async () => {
    try {
      const res = await api(`/api/forum/posts/${postId}`);
      setState({ loading: false, post: res.post, replies: res.replies || [] });
    } catch (e) {
      setState({ loading: false, post: null, replies: [] });
      toast(e.message, { kind: "error" });
    }
  }, [postId, toast]);

  useEffect(() => { load(); }, [load]);

  // One vote handler for the post and every reply: the server takes the state
  // you want (not a toggle), so a double tap settles rather than flip-flops.
  const vote = async (kind, id, current) => {
    if (!user) { openAuth(); return; }
    const want = !current.voted;
    setBusyVote(id);
    // Optimistic: move the number now, put it back if the server says no.
    const patch = (v, delta) => ({ ...v, voted: want, votes: Math.max(0, v.votes + delta) });
    const apply = (fn) => setState((s) => (kind === "posts"
      ? { ...s, post: fn(s.post) }
      : { ...s, replies: s.replies.map((r) => (r.id === id ? fn(r) : r)) }));
    apply((v) => patch(v, want ? 1 : -1));
    try {
      const res = await postJson(`/api/forum/${kind}/${id}/vote`, { on: want });
      apply((v) => ({ ...v, voted: res.voted, votes: res.votes }));
      if (kind === "posts") onChanged?.();
    } catch (e) {
      apply((v) => patch(v, want ? -1 : 1));
      toast(e.message, { kind: "error" });
    } finally {
      setBusyVote("");
    }
  };

  const sendReply = once("forum-reply", async () => {
    if (!user) { openAuth(); return; }
    setPosting(true);
    try {
      const res = await postJson(`/api/forum/posts/${postId}/replies`, { body: reply });
      setReply("");
      setState((s) => ({
        ...s,
        replies: [...s.replies, res.reply],
        post: { ...s.post, replies: s.post.replies + 1 },
      }));
      onChanged?.();
    } catch (e) {
      toast(e.message, { kind: "error" });
    } finally {
      setPosting(false);
    }
  });

  const savePost = async (body) => {
    setSaving(true);
    try {
      const res = await api(`/api/forum/posts/${postId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ body }),
      });
      setState((s) => ({ ...s, post: res.post }));
      setEditing(null);
      onChanged?.();
    } catch (e) {
      toast(e.message, { kind: "error" });
    } finally {
      setSaving(false);
    }
  };

  const saveReply = async (id, body) => {
    setSaving(true);
    try {
      const res = await api(`/api/forum/replies/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ body }),
      });
      setState((s) => ({
        ...s, replies: s.replies.map((r) => (r.id === id ? res.reply : r)),
      }));
      setEditing(null);
    } catch (e) {
      toast(e.message, { kind: "error" });
    } finally {
      setSaving(false);
    }
  };

  const removePost = async () => {
    if (!window.confirm("Delete this thread? Its replies go with it.")) return;
    try {
      await api(`/api/forum/posts/${postId}`, { method: "DELETE" });
      toast("Thread deleted.", { kind: "success" });
      onChanged?.();
      onBack();
    } catch (e) {
      toast(e.message, { kind: "error" });
    }
  };

  const removeReply = async (id) => {
    if (!window.confirm("Delete this reply?")) return;
    try {
      await api(`/api/forum/replies/${id}`, { method: "DELETE" });
      setState((s) => ({
        ...s,
        replies: s.replies.filter((r) => r.id !== id),
        post: { ...s.post, replies: Math.max(0, s.post.replies - 1) },
      }));
      onChanged?.();
    } catch (e) {
      toast(e.message, { kind: "error" });
    }
  };

  if (state.loading) {
    return (
      <div className="flex items-center justify-center py-20 text-ink-secondary">
        <Loader2 size={20} className="animate-spin" aria-hidden />
      </div>
    );
  }
  if (!state.post) {
    return (
      <EmptyState
        illustration={SearchIllustration}
        title="That thread is gone"
        message="It may have been deleted by whoever wrote it."
        action={<Button variant="primary" onClick={onBack}>Back to the board</Button>}
      />
    );
  }

  const { post } = state;
  const mine = user && post.user_id === user.id;

  return (
    <div className="max-w-3xl">
      <Button variant="ghost" size="sm" onClick={onBack} className="mb-3 -ml-2">
        <ArrowLeft size={16} aria-hidden /> Community
      </Button>

      <Card>
        <div className="flex gap-3.5">
          <Avatar name={post.author} />
          <div className="min-w-0 flex-1">
            <h1 className="text-xl font-bold text-ink leading-snug">{post.title}</h1>
            <div className="mt-1">
              <Byline author={post.author} at={post.created_at} edited={post.edited}
                      category={post.category} categoryLabel={label(post.category)} />
            </div>
          </div>
          {mine && (
            <OwnerActions what="post" onEdit={() => setEditing("post")}
                          onDelete={removePost} />
          )}
        </div>

        {editing === "post" ? (
          <InlineEditor initial={post.body} rows={6} saving={saving}
                        max={limits.body_max || 8000}
                        onCancel={() => setEditing(null)} onSave={savePost} />
        ) : (
          <p className="mt-3 text-[15px] leading-relaxed text-ink whitespace-pre-wrap break-words">
            {post.body}
          </p>
        )}

        <AttachmentCard
          attachment={post.attachment}
          onOpen={mine && post.listing_id
            ? () => openListing(post.listing_id) : undefined}
        />

        <div className="mt-4 flex items-center gap-3">
          <VoteButton votes={post.votes} voted={post.voted} busy={busyVote === post.id}
                      label="this post"
                      onVote={() => vote("posts", post.id, post)} />
          <span className="text-[13px] text-ink-secondary">
            {post.replies === 1 ? "1 reply" : `${post.replies} replies`}
          </span>
        </div>
      </Card>

      <div className="mt-4 flex flex-col gap-3">
        <AnimatePresence initial={false}>
          {state.replies.map((r) => {
            const isMine = user && r.user_id === user.id;
            return (
              <motion.div
                key={r.id}
                layout="position"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
              >
                <Card className="p-4">
                  <div className="flex gap-3">
                    <Avatar name={r.author} className="size-8 text-[11px]" />
                    <div className="min-w-0 flex-1">
                      <Byline author={r.author} at={r.created_at} edited={r.edited} />
                      {editing === r.id ? (
                        <InlineEditor initial={r.body} saving={saving}
                                      max={limits.reply_max || 4000}
                                      onCancel={() => setEditing(null)}
                                      onSave={(body) => saveReply(r.id, body)} />
                      ) : (
                        <p className="mt-1.5 text-sm leading-relaxed text-ink whitespace-pre-wrap break-words">
                          {r.body}
                        </p>
                      )}
                      <div className="mt-2.5">
                        <VoteButton votes={r.votes} voted={r.voted}
                                    busy={busyVote === r.id} label="this reply"
                                    onVote={() => vote("replies", r.id, r)} />
                      </div>
                    </div>
                    {isMine && (
                      <OwnerActions what="reply" onEdit={() => setEditing(r.id)}
                                    onDelete={() => removeReply(r.id)} />
                    )}
                  </div>
                </Card>
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>

      <Card className="mt-4 p-4">
        {user ? (
          <div className="flex flex-col gap-2">
            <Textarea
              rows={3}
              value={reply}
              maxLength={limits.reply_max || 4000}
              onChange={(e) => setReply(e.target.value)}
              placeholder="Answer, or add what you'd have done differently…"
            />
            <div className="flex justify-end">
              <Button variant="primary" size="sm" onClick={sendReply}
                      loading={posting} disabled={!reply.trim()}>
                <Send size={15} aria-hidden /> Reply
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <p className="text-sm text-ink-secondary">
              Log in to answer — reading is open to everyone.
            </p>
            <Button variant="primary" size="sm" onClick={() => openAuth()}>
              Log in
            </Button>
          </div>
        )}
      </Card>
    </div>
  );
}

// --- the view ---------------------------------------------------------------

export function ForumView() {
  const { user, openAuth, navTick } = useApp();
  const { toast } = useToast();

  const [meta, setMeta] = useState({
    available: true, categories: [], limits: {},
    stats: { posts: 0, replies: 0, members: 0 },
  });
  const [posts, setPosts] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [category, setCategory] = useState("");
  const [sort, setSort] = useState("recent");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");     // the debounced value we send
  const [mine, setMine] = useState(false);
  const [openId, setOpenId] = useState(null);
  const [composing, setComposing] = useState(false);
  const [voting, setVoting] = useState("");

  const label = useCallback(
    (id) => meta.categories.find((c) => c.id === id)?.label || id,
    [meta.categories],
  );

  // Pressing "Community" while a thread is open means "take me back to the
  // board" — the view never unmounts, so nothing else would reset it.
  useEffect(() => { setOpenId(null); }, [navTick]);

  useEffect(() => {
    let alive = true;
    api("/api/forum/meta")
      .then((m) => { if (alive) setMeta(m); })
      .catch(() => {});
    return () => { alive = false; };
  }, [user]);

  // Typing shouldn't fire a query per keystroke.
  useEffect(() => {
    const t = setTimeout(() => setQuery(search.trim()), 300);
    return () => clearTimeout(t);
  }, [search]);

  const loadPage = useCallback(async ({ append = false } = {}) => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        sort, limit: String(PAGE_SIZE),
        offset: String(append ? posts.length : 0),
      });
      if (category) params.set("category", category);
      if (query) params.set("q", query);
      if (mine) params.set("mine", "true");
      const res = await api(`/api/forum/posts?${params}`);
      setPosts((prev) => (append ? [...prev, ...(res.posts || [])] : res.posts || []));
      setTotal(res.total || 0);
    } catch (e) {
      toast(`Couldn't load the community: ${e.message}`, { kind: "error" });
    } finally {
      setLoading(false);
    }
    // posts.length is read for the append offset only — including it here
    // would re-run the first page every time a page lands.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sort, category, query, mine, toast]);

  useEffect(() => { loadPage(); }, [loadPage, user]);

  const refreshMeta = useCallback(() => {
    api("/api/forum/meta").then(setMeta).catch(() => {});
  }, []);

  const vote = async (post) => {
    if (!user) { openAuth(); return; }
    const want = !post.voted;
    setVoting(post.id);
    const patch = (p, delta) => (p.id === post.id
      ? { ...p, voted: want, votes: Math.max(0, p.votes + delta) } : p);
    setPosts((ps) => ps.map((p) => patch(p, want ? 1 : -1)));
    try {
      const res = await postJson(`/api/forum/posts/${post.id}/vote`, { on: want });
      setPosts((ps) => ps.map((p) => (p.id === post.id
        ? { ...p, voted: res.voted, votes: res.votes } : p)));
    } catch (e) {
      setPosts((ps) => ps.map((p) => patch(p, want ? -1 : 1)));
      toast(e.message, { kind: "error" });
    } finally {
      setVoting("");
    }
  };

  const startThread = () => {
    if (!user) { openAuth(); return; }
    setComposing(true);
  };

  if (openId) {
    return (
      <Thread
        postId={openId}
        categories={meta.categories}
        limits={meta.limits || {}}
        onBack={() => setOpenId(null)}
        onChanged={() => { loadPage(); refreshMeta(); }}
      />
    );
  }

  // No database on this server — the board has nowhere to keep anything, and
  // saying so beats an empty list that looks like a quiet community.
  if (meta.available === false) {
    return (
      <EmptyState
        illustration={SearchIllustration}
        title="The community isn't switched on here"
        message="This server has no database configured, so there's nowhere to keep threads. Everything else in the app works as usual."
      />
    );
  }

  const showing = posts.length;

  return (
    <div className="max-w-4xl">
      <Card className="mb-4">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-3 min-w-0">
            <span className="grid place-items-center size-11 rounded-[14px] bg-blue-soft text-blue shrink-0">
              <MessagesSquare size={21} strokeWidth={2} aria-hidden />
            </span>
            <div className="min-w-0">
              <h1 className="text-xl font-bold text-ink leading-tight">Community</h1>
              <p className="text-[13px] text-ink-secondary">
                {meta.stats.posts === 0
                  ? "Nobody's posted yet — go first."
                  : `${meta.stats.posts} threads · ${meta.stats.replies} replies · ${meta.stats.members} sellers`}
              </p>
            </div>
          </div>
          <Button variant="primary" onClick={startThread}>
            <Plus size={17} aria-hidden /> New thread
          </Button>
        </div>
      </Card>

      <AnimatePresence>
        {composing && (
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
          >
            <Composer
              categories={meta.categories}
              limits={meta.limits || {}}
              drafts={{ category: category || "general" }}
              onCancel={() => setComposing(false)}
              onPosted={(post) => {
                setComposing(false);
                setPosts((ps) => [post, ...ps]);
                setTotal((t) => t + 1);
                refreshMeta();
                setOpenId(post.id);
              }}
            />
          </motion.div>
        )}
      </AnimatePresence>

      {/* Sections. "All" first so the board reads as one room by default. */}
      <div className="flex gap-2 overflow-x-auto pb-2 -mx-1 px-1">
        {[{ id: "", label: "All", blurb: "Every section" }, ...meta.categories].map((c) => (
          <button
            key={c.id || "all"}
            type="button"
            title={c.blurb}
            onClick={() => setCategory(c.id)}
            className={cn(
              "shrink-0 rounded-full px-3.5 h-9 text-[13px] font-semibold",
              "transition-colors duration-150 cursor-pointer border",
              category === c.id
                ? "bg-blue text-on-accent border-blue"
                : "bg-card text-ink-secondary border-line hover:text-ink hover:border-line-strong",
            )}
          >
            {c.label}
          </button>
        ))}
      </div>

      <div className="flex items-center gap-2 flex-wrap my-3">
        <div className="relative flex-1 min-w-[200px]">
          <Search size={16} aria-hidden
                  className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-faint" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search the community"
            className="pl-9 h-10"
            aria-label="Search the community"
          />
        </div>
        <div className="flex gap-1 bg-bg-sunken rounded-button p-1">
          {SORTS.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => setSort(s.id)}
              className={cn(
                "rounded-[10px] px-2.5 h-8 text-[13px] font-semibold cursor-pointer",
                "transition-colors duration-150",
                sort === s.id ? "bg-card text-ink shadow-card" : "text-ink-secondary hover:text-ink",
              )}
            >
              {s.label}
            </button>
          ))}
        </div>
        {user && (
          <button
            type="button"
            onClick={() => setMine((m) => !m)}
            className={cn(
              "rounded-full px-3.5 h-9 text-[13px] font-semibold border cursor-pointer",
              "transition-colors duration-150",
              mine ? "bg-blue text-on-accent border-blue"
                : "bg-card text-ink-secondary border-line hover:text-ink",
            )}
          >
            My threads
          </button>
        )}
      </div>

      <Card className="p-0 overflow-hidden">
        {loading && posts.length === 0 ? (
          <div className="flex items-center justify-center py-16 text-ink-secondary">
            <Loader2 size={20} className="animate-spin" aria-hidden />
          </div>
        ) : posts.length === 0 ? (
          <EmptyState
            illustration={SearchIllustration}
            title={query || category || mine ? "Nothing here yet" : "The room is quiet"}
            message={query || category || mine
              ? "Try another section, or clear the search."
              : "Ask what something's worth, show a flip you're proud of, or find out how everyone else ships the awkward stuff."}
            action={
              <Button variant="primary" onClick={startThread}>
                <Plus size={17} aria-hidden /> Start the first thread
              </Button>
            }
          />
        ) : (
          <AnimatePresence initial={false}>
            {posts.map((p) => (
              <PostRow
                key={p.id}
                post={p}
                categoryLabel={label(p.category)}
                voting={voting === p.id}
                onOpen={() => setOpenId(p.id)}
                onVote={() => vote(p)}
              />
            ))}
          </AnimatePresence>
        )}
      </Card>

      {showing < total && (
        <div className="flex justify-center mt-4">
          <Button onClick={() => loadPage({ append: true })} loading={loading}>
            Load more ({total - showing} left)
          </Button>
        </div>
      )}
    </div>
  );
}
