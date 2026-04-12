import { useState, useEffect, useRef } from "react";
import notesData from "./notes-content.json";

const PASSCODE = "121672";

function parseMarkdown(md: string): string {
  let html = md
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  const lines = html.split("\n");
  const result: string[] = [];
  let inCode = false;
  let inList = false;
  let listBuffer: string[] = [];

  const flushList = () => {
    if (listBuffer.length > 0) {
      result.push(`<ul class="my-2 pl-5 space-y-1">${listBuffer.join("")}</ul>`);
      listBuffer = [];
      inList = false;
    }
  };

  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i];

    if (raw.startsWith("```")) {
      if (!inCode) {
        flushList();
        inCode = true;
        result.push(`<pre class="bg-gray-900 border border-gray-700 rounded p-3 my-3 overflow-x-auto text-xs font-mono text-green-400"><code>`);
      } else {
        inCode = false;
        result.push(`</code></pre>`);
      }
      continue;
    }

    if (inCode) {
      result.push(raw + "\n");
      continue;
    }

    if (raw.startsWith("# ")) {
      flushList();
      result.push(`<h1 class="text-2xl font-bold text-yellow-400 mt-8 mb-3 border-b border-yellow-400/30 pb-2">${inline(raw.slice(2))}</h1>`);
    } else if (raw.startsWith("## ")) {
      flushList();
      result.push(`<h2 class="text-xl font-bold text-cyan-400 mt-6 mb-2">${inline(raw.slice(3))}</h2>`);
    } else if (raw.startsWith("### ")) {
      flushList();
      result.push(`<h3 class="text-lg font-semibold text-emerald-400 mt-4 mb-2">${inline(raw.slice(4))}</h3>`);
    } else if (raw.startsWith("#### ")) {
      flushList();
      result.push(`<h4 class="text-base font-semibold text-purple-400 mt-3 mb-1">${inline(raw.slice(5))}</h4>`);
    } else if (raw.match(/^-{3,}$/) || raw.match(/^\*{3,}$/)) {
      flushList();
      result.push(`<hr class="border-gray-700 my-4" />`);
    } else if (raw.startsWith("- ") || raw.startsWith("* ")) {
      inList = true;
      listBuffer.push(`<li class="text-gray-300 text-sm leading-relaxed">${inline(raw.slice(2))}</li>`);
    } else if (raw.match(/^\d+\. /)) {
      inList = true;
      const text = raw.replace(/^\d+\. /, "");
      listBuffer.push(`<li class="text-gray-300 text-sm leading-relaxed">${inline(text)}</li>`);
    } else if (raw.startsWith("> ")) {
      flushList();
      result.push(`<blockquote class="border-l-4 border-yellow-500 pl-3 my-2 text-gray-400 italic text-sm">${inline(raw.slice(2))}</blockquote>`);
    } else if (raw.trim() === "") {
      flushList();
      result.push(`<div class="h-2"></div>`);
    } else {
      flushList();
      result.push(`<p class="text-gray-300 text-sm leading-relaxed my-1">${inline(raw)}</p>`);
    }
  }

  flushList();
  return result.join("");
}

function inline(text: string): string {
  return text
    .replace(/\*\*\*(.+?)\*\*\*/g, "<strong><em>$1</em></strong>")
    .replace(/\*\*(.+?)\*\*/g, `<strong class="text-white font-semibold">$1</strong>`)
    .replace(/\*(.+?)\*/g, "<em class=\"text-gray-200\">$1</em>")
    .replace(/`(.+?)`/g, `<code class="bg-gray-800 text-green-400 px-1 rounded text-xs font-mono">$1</code>`)
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, `<a href="$2" target="_blank" class="text-cyan-400 hover:text-cyan-300 underline">$1</a>`);
}

function Passcode({ onUnlock }: { onUnlock: () => void }) {
  const [input, setInput] = useState("");
  const [error, setError] = useState(false);
  const [shake, setShake] = useState(false);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (input === PASSCODE) {
      onUnlock();
    } else {
      setError(true);
      setShake(true);
      setTimeout(() => setShake(false), 500);
      setTimeout(() => {
        setError(false);
        setInput("");
      }, 1500);
    }
  };

  return (
    <div className="min-h-screen bg-gray-950 flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <div className="text-yellow-400 text-4xl font-black tracking-tight mb-1">EdgeIQ</div>
          <div className="text-gray-500 text-sm">Build Notes & Product Roadmap</div>
        </div>
        <form onSubmit={handleSubmit} className={`${shake ? "animate-pulse" : ""}`}>
          <div className="bg-gray-900 border border-gray-700 rounded-xl p-6 space-y-4">
            <label className="block text-gray-400 text-xs font-medium uppercase tracking-widest mb-1">
              Access Code
            </label>
            <input
              type="password"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Enter passcode"
              className={`w-full bg-gray-800 border ${error ? "border-red-500 text-red-400" : "border-gray-600"} rounded-lg px-4 py-3 text-white text-center text-xl tracking-widest focus:outline-none focus:border-yellow-400 transition-colors`}
              autoFocus
              maxLength={10}
            />
            {error && <p className="text-red-400 text-xs text-center">Invalid passcode</p>}
            <button
              type="submit"
              className="w-full bg-yellow-400 hover:bg-yellow-300 text-gray-900 font-bold py-3 rounded-lg transition-colors text-sm uppercase tracking-wider"
            >
              Unlock
            </button>
          </div>
        </form>
        <p className="text-gray-700 text-xs text-center mt-4">Private build documentation</p>
      </div>
    </div>
  );
}

function TableOfContents({ sections, activeId, onNav }: { sections: { id: string; text: string; level: number }[]; activeId: string; onNav: (id: string) => void }) {
  return (
    <div className="w-64 flex-shrink-0 hidden lg:block">
      <div className="fixed top-0 left-0 w-64 h-screen bg-gray-900 border-r border-gray-800 overflow-y-auto p-4">
        <div className="text-yellow-400 font-black text-lg mb-1">EdgeIQ</div>
        <div className="text-gray-500 text-xs mb-4">Build Notes</div>
        <nav className="space-y-0.5">
          {sections.map((s) => (
            <button
              key={s.id}
              onClick={() => onNav(s.id)}
              className={`block w-full text-left text-xs py-1 px-2 rounded transition-colors truncate
                ${s.level === 1 ? "font-bold text-gray-300 hover:text-yellow-400 mt-2" : ""}
                ${s.level === 2 ? "pl-3 text-gray-400 hover:text-cyan-400" : ""}
                ${s.level === 3 ? "pl-5 text-gray-500 hover:text-emerald-400 text-xs" : ""}
                ${activeId === s.id ? "text-yellow-400 bg-yellow-400/10" : ""}
              `}
            >
              {s.text}
            </button>
          ))}
        </nav>
      </div>
    </div>
  );
}

function NotesViewer() {
  const [html, setHtml] = useState("");
  const [sections, setSections] = useState<{ id: string; text: string; level: number }[]>([]);
  const [activeId, setActiveId] = useState("");
  const [search, setSearch] = useState("");
  const contentRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const md = notesData.content;
    const rendered = parseMarkdown(md);

    const sectionList: { id: string; text: string; level: number }[] = [];
    const lines = md.split("\n");
    lines.forEach((line, i) => {
      const h1 = line.match(/^# (.+)$/);
      const h2 = line.match(/^## (.+)$/);
      const h3 = line.match(/^### (.+)$/);
      if (h1) sectionList.push({ id: `h-${i}`, text: h1[1], level: 1 });
      else if (h2) sectionList.push({ id: `h-${i}`, text: h2[1], level: 2 });
      else if (h3) sectionList.push({ id: `h-${i}`, text: h3[1], level: 3 });
    });

    setSections(sectionList);
    setHtml(rendered);
  }, []);

  const handleNav = (id: string) => {
    setActiveId(id);
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const lastUpdated = notesData.content.match(/\*Last updated: (.+?)\*/)?.[1] || "";

  return (
    <div className="min-h-screen bg-gray-950 flex">
      <TableOfContents sections={sections} activeId={activeId} onNav={handleNav} />
      <div className="flex-1 lg:ml-64">
        <div className="sticky top-0 z-10 bg-gray-950/95 backdrop-blur border-b border-gray-800 px-4 py-3 flex items-center gap-4">
          <div className="text-yellow-400 font-black text-sm lg:hidden">EdgeIQ</div>
          <input
            type="text"
            placeholder="Search notes..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="flex-1 max-w-sm bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-yellow-400 transition-colors"
          />
          {lastUpdated && (
            <span className="hidden md:block text-gray-600 text-xs ml-auto">Updated: {lastUpdated}</span>
          )}
        </div>
        <div className="max-w-4xl mx-auto px-4 md:px-8 pb-16 pt-4">
          {search ? (
            <SearchResults content={notesData.content} query={search} />
          ) : (
            <div
              ref={contentRef}
              dangerouslySetInnerHTML={{ __html: addIds(html, sections) }}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function addIds(html: string, sections: { id: string; text: string; level: number }[]): string {
  let result = html;
  sections.forEach((s) => {
    const escaped = s.text.replace(/[.*+?^${}()|[\]\\&]/g, "\\$&");
    result = result.replace(
      new RegExp(`(<h[1-3][^>]*>)(${escaped})(</h[1-3]>)`),
      `$1<span id="${s.id}" class="scroll-mt-20">$2</span>$3`
    );
  });
  return result;
}

function SearchResults({ content, query }: { content: string; query: string }) {
  const lines = content.split("\n");
  const lq = query.toLowerCase();
  const matches = lines
    .map((line, i) => ({ line, i }))
    .filter(({ line }) => line.toLowerCase().includes(lq));

  if (matches.length === 0) {
    return <p className="text-gray-500 text-sm mt-8 text-center">No results for "{query}"</p>;
  }

  return (
    <div className="space-y-3 mt-4">
      <p className="text-gray-500 text-xs">{matches.length} matches for "{query}"</p>
      {matches.map(({ line, i }) => {
        const start = Math.max(0, i - 1);
        const ctx = lines.slice(start, i + 3).join("\n");
        const highlighted = line.replace(
          new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi"),
          (m) => `<mark class="bg-yellow-400/30 text-yellow-300 rounded px-0.5">${m}</mark>`
        );
        return (
          <div key={i} className="bg-gray-900 border border-gray-700 rounded-lg p-3">
            <p
              className="text-sm text-gray-300"
              dangerouslySetInnerHTML={{ __html: highlighted }}
            />
            <p className="text-xs text-gray-600 mt-1">Line {i + 1}</p>
          </div>
        );
      })}
    </div>
  );
}

export default function App() {
  const [unlocked, setUnlocked] = useState(false);

  useEffect(() => {
    if (sessionStorage.getItem("edgeiq_notes_unlocked") === "1") {
      setUnlocked(true);
    }
  }, []);

  const handleUnlock = () => {
    sessionStorage.setItem("edgeiq_notes_unlocked", "1");
    setUnlocked(true);
  };

  if (!unlocked) return <Passcode onUnlock={handleUnlock} />;
  return <NotesViewer />;
}
