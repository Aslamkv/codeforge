"""What Tinker knows, as one graph (Graph): each language, topic, lesson, fact, finding, fix and run is a node,
linked to what it belongs to, what it cites, and the runs that taught it, proved it or found it wrong. The
page that shows it (GraphPage) is one file a browser opens with no network: the graph drawn when scripts
run, and a plain list of everything when they do not (a phone's file preview)."""

import html
import itertools
import json

from .knowledge import Embeddings, Library
from .settings import Home, SIMILAR_MEANING, SIMILAR_WORDS, first_line, similarity
from .workspace import TaughtCommands, Toolchain

HISTORY_LINKS = {"born": "taught_by", "confirmed": "confirmed_in", "helped": "helped_in", "failed": "failed_in"}


class Graph:
    """Nodes {id, kind, label, text, status, group, details, sources} and links {source, target, kind}, read
    from Tinker's home. similar_by says how lessons were found similar (by meaning, or by words and why)."""

    def __init__(self):
        self.nodes, self.links, self.similar_by = {}, [], ""

    @classmethod
    def build(cls, embeddings=None):
        """The graph of everything Tinker keeps; embeddings (or None) find the lessons that are alike."""
        graph = cls()
        graph.add_languages()
        graph.add_any_language()
        graph.add_topics()
        graph.add_commands()
        graph.add_runs()
        graph.add_similar(embeddings)
        return graph

    def add_languages(self):
        for key, doc in Home.store().items("languages").items():
            hub = self.hub(f"lang:{key}", "language", doc.get("language", key), key,
                           [("projects finished", doc.get("projects", 0))])
            if Toolchain(doc.get("toolchain")):
                self.item(f"toolchain:{key}", "toolchain", Toolchain(doc["toolchain"]).summary(), hub,
                          status="proven", details=[("saved", doc.get("toolchain_saved", "?"))])
            for fix in doc.get("fixes", []):
                self.add_fix(key, fix, hub)
            for fact in doc.get("facts", []):
                self.add_claim(f"known:{fact['id']}", "fact", fact, hub)
            for lesson in doc.get("lessons", []):
                self.add_lesson(lesson, hub)
            self.add_retired(doc, hub, key)

    def add_any_language(self):
        doc = Home.store().get("memory", "any") or {}
        hub = self.hub("any", "language", "Any language", "any", [])
        for lesson in doc.get("lessons", []):
            self.add_lesson(lesson, hub)
        self.add_retired(doc, hub, "any")

    def add_topics(self):
        """Each topic learned from the web: a language's joins its language's node; its facts link to the proven
        facts they became, its findings to the facts they cite, and its rules to the facts and lessons they rest on."""
        for slug, entry in Library.topics().items():
            hub = f"lang:{slug}" if entry.get("kind") == "language" else f"topic:{slug}"
            details = [("learned", entry.get("learned", "?")), ("summary", entry.get("summary", ""))]
            hub = self.hub(hub, "language" if hub.startswith("lang:") else "topic", entry.get("name", slug), slug,
                           details)
            for fact in entry.get("facts", []):
                self.add_claim(f"fact:{slug}:{fact['id']}", "fact", fact, hub)
                if f"known:{fact['id']}" in self.nodes:
                    self.link(f"fact:{slug}:{fact['id']}", f"known:{fact['id']}", "became")
            for finding in entry.get("findings", []):
                node = self.add_claim(f"finding:{slug}:{finding['id']}", "finding", finding, hub)
                for fact_id in finding.get("cites", []):
                    self.link(node, f"fact:{slug}:{fact_id}", "cites")
            for rule in entry.get("rules", []):
                node = self.add_claim(f"rule:{slug}:{rule['id']}", "rule", rule, hub)
                for cited in rule.get("cites", []):
                    self.link(node, f"fact:{slug}:{cited}" if f"fact:{slug}:{cited}" in self.nodes
                              else f"lesson:{cited}", "cites")
            self.add_retired(entry, hub, slug)

    def add_commands(self):
        taught = TaughtCommands.all()
        if taught:
            hub = self.hub("commands", "commands", "Your commands", "commands", [])
            for name, entry in taught.items():
                self.item(f"command:{name}", "command", f"{name}: {TaughtCommands.describe(entry)}", hub,
                          status="proven")

    def add_runs(self):
        """A node for each run an item's history names (with its line from "runs", when one is kept), linked
        from the items it taught, confirmed, helped or failed."""
        lines = Home.store().items("runs")
        for node in list(self.nodes.values()):
            for event in node.pop("history", []):
                if event.get("run"):
                    self.add_run(event["run"], lines.get(event["run"]))
                    self.link(node["id"], f"run:{event['run']}", HISTORY_LINKS.get(event.get("event"), "taught_by"))

    def add_run(self, run_id, line):
        if f"run:{run_id}" in self.nodes:
            return
        text = line.get("task", "") if line else "no line about this run is kept (tinker clear runs, or older)"
        details = [("status", line.get("status", "")), ("language", line.get("language", "")),
                   ("started", line.get("created", "")), ("updated", line.get("updated", ""))] if line else []
        self.node(f"run:{run_id}", "run", f"@{run_id}", text, line.get("status") if line else None, "runs", details)

    def add_similar(self, embeddings):
        """Lessons that say much the same, linked: by meaning with the embedding model, else by their words."""
        lessons = [node for node in self.nodes.values() if node["kind"] == "lesson" and node["status"] != "retired"]
        vectors = embeddings.vectors([n["text"] for n in lessons], keep=False) if embeddings and lessons else None
        if vectors:
            self.similar_by = f"meaning ({embeddings.name})"
            alike = lambda a, b: Embeddings.cosine(vectors[a], vectors[b]) >= SIMILAR_MEANING
        else:
            why = embeddings.failed if embeddings else "no embedding model selected (tinker embed NAME)"
            self.similar_by = f"words ({why})" if lessons else "words"
            alike = lambda a, b: similarity(lessons[a]["text"], lessons[b]["text"]) >= SIMILAR_WORDS
        for a, b in itertools.combinations(range(len(lessons)), 2):
            if alike(a, b):
                self.link(lessons[a]["id"], lessons[b]["id"], "similar")

    def add_lesson(self, lesson, hub):
        details = [("phase", lesson.get("phase")), ("seen", lesson.get("seen", 1)), ("helped", lesson.get("helped", 0)),
                   ("added", lesson.get("added")), ("last seen", lesson.get("last_seen"))]
        return self.item(f"lesson:{lesson['id']}", "lesson", lesson["text"], hub, lesson.get("status", "candidate"),
                         details, history=lesson.get("history", []))

    def add_claim(self, node_id, kind, claim, hub):
        """A fact, a finding or a rule, with its confidence and its pages."""
        text = claim.get("text") or claim.get("answer", "")
        details = [("phase", claim.get("phase")), ("confidence", claim.get("confidence")),
                   ("helped", claim.get("helped", 0))]
        sources = [s for s in [claim.get("source")] + list(claim.get("also", [])) + list(claim.get("sites", []))
                   if isinstance(s, str) and s.startswith("http")]
        return self.item(node_id, kind, text, hub, claim.get("status", "candidate"), details,
                         history=claim.get("history", []), sources=sources)

    def add_fix(self, key, fix, hub):
        details = [("helped", fix.get("used", 0)), ("added", fix.get("added")),
                   ("replayed without the model", "yes" if fix.get("replay") else "no")]
        return self.item(f"fix:{key}:{fix['failure']}", "fix", f"{fix['failure']} → {fix.get('cause', '')}", hub,
                         fix.get("status", "proven"), details, history=fix.get("history", []))

    def add_retired(self, doc, hub, key):
        """What was retired from this language or topic: shown for people, with why."""
        for number, item in enumerate(doc.get("retired", [])):
            kind = {"lessons": "lesson", "fixes": "fix", "findings": "finding", "rules": "rule"}.get(item.get("was"), "fact")
            text = item.get("text") or item.get("answer") or f"{item.get('failure', '')} → {item.get('cause', '')}"
            details = [("was", item.get("was")), ("why", item.get("reason")), ("retired", item.get("retired")),
                       ("phase", item.get("phase"))]
            self.item(f"retired:{key}:{number}", kind, text, hub, "retired", details, history=item.get("history", []))

    def hub(self, node_id, kind, label, group, details):
        """A language, a topic, any language or the person's commands: what the items belong to."""
        if node_id in self.nodes:
            self.nodes[node_id]["details"] += details
        else:
            self.node(node_id, kind, label, label, None, group, details)
        return node_id

    def item(self, node_id, kind, text, hub, status, details=(), history=(), sources=()):
        node = self.node(node_id, kind, first_line(text, 60), text, status, self.nodes[hub]["group"], details)
        node["history"], node["sources"] = list(history), list(dict.fromkeys(sources))
        self.link(node_id, hub, "belongs_to")
        return node_id

    def node(self, node_id, kind, label, text, status, group, details):
        self.nodes[node_id] = {"id": node_id, "kind": kind, "label": label, "text": text, "status": status,
                               "group": group, "details": [[k, str(v)] for k, v in details if v not in (None, "")],
                               "sources": []}
        return self.nodes[node_id]

    def link(self, source, target, kind):
        if source in self.nodes and target in self.nodes:
            self.links.append({"source": source, "target": target, "kind": kind})

    def counts(self):
        """{kind: how many}, the runs and the retired apart."""
        found = {}
        for node in self.nodes.values():
            kind = "retired" if node["status"] == "retired" else node["kind"]
            if node["id"] != "any":  # any language is where lessons for every language go, not a language
                found[kind] = found.get(kind, 0) + 1
        return found

    def summary(self):
        """What the graph holds, in one line."""
        counts, many = self.counts(), {"fix": "fixes", "retired": "retired"}
        shown = [f"{counts[k]} {k if counts[k] == 1 else many.get(k, k + 's')}" for k in
                 ("language", "topic", "lesson", "rule", "fact", "finding", "fix", "toolchain", "command", "run", "retired")
                 if counts.get(k)]
        return (", ".join(shown) or "nothing learned yet") + f"; {len(self.links)} links"

    def to_json(self):
        return {"nodes": list(self.nodes.values()), "links": self.links, "similar_by": self.similar_by}


class GraphPage:
    """The page: the graph as data inside it, a plain list of every node (readable without scripts), and the
    script that draws the graph. Nothing is fetched from elsewhere."""
    KIND_NAMES = {"language": "Languages", "topic": "Topics", "commands": "Your commands", "lesson": "Lessons",
                  "rule": "Rules", "fact": "Facts", "finding": "Findings", "fix": "Toolchain fixes", "toolchain": "Toolchains",
                  "command": "Commands", "run": "Runs"}

    @classmethod
    def html(cls, graph):
        data = json.dumps(graph.to_json(), ensure_ascii=False).replace("</", "<\\/")
        return (PAGE.replace("__SUMMARY__", html.escape(graph.summary()))
                .replace("__SIMILAR__", html.escape(graph.similar_by or "words"))
                .replace("__LIST__", cls.plain_list(graph)).replace("__DATA__", data))

    @classmethod
    def plain_list(cls, graph):
        """Every node by its group (a language, a topic, the runs), then by kind."""
        groups = {}
        for node in graph.nodes.values():
            groups.setdefault(node["group"], []).append(node)
        return "\n".join(cls.group_section(group, nodes) for group, nodes in groups.items())

    @classmethod
    def group_section(cls, group, nodes):
        hubs = [n for n in nodes if n["kind"] in ("language", "topic", "commands")]
        title = hubs[0]["label"] if hubs else ("Runs" if group == "runs" else group)
        kinds = {}
        for node in nodes:
            if node not in hubs:
                kinds.setdefault("retired" if node["status"] == "retired" else node["kind"], []).append(node)
        parts = [f"<section><h2>{html.escape(title)}</h2>"]
        for kind, members in kinds.items():
            name = "Retired" if kind == "retired" else cls.KIND_NAMES.get(kind, kind)
            items = "".join(cls.list_item(node) for node in members)
            parts.append(f"<details><summary>{html.escape(name)} ({len(members)})</summary><ul>{items}</ul></details>")
        return "".join(parts) + "</section>"

    @staticmethod
    def list_item(node):
        tags = [node["status"]] if node["status"] else []
        tags += [f"{k} {v}" for k, v in node["details"] if k in ("phase", "seen", "helped", "why", "status")]
        sources = "".join(f' <a href="{html.escape(s)}">source</a>' for s in node["sources"])
        return (f"<li><span class=tags>{html.escape(' · '.join(tags))}</span> {html.escape(node['text'])}"
                f"{sources}</li>")


def write_page(embeddings=None):
    """The page written to Tinker's home; the graph it shows."""
    graph = Graph.build(embeddings)
    Home.graph_page().write_text(GraphPage.html(graph), encoding="utf-8")
    return graph


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tinker's memory</title>
<style>
:root{--bg:#fbfaf8;--panel:#ffffff;--ink:#1d1d1f;--muted:#6b6b70;--line:#e3e1dc;--link:#c9c6bf;
--language:#3a5ba0;--topic:#7a4fa0;--commands:#2f7d6d;--lesson:#d0782a;--fact:#3d8bd0;--finding:#a04f7a;
--rule:#2f8f5b;--fix:#b33b3b;--toolchain:#5d7f2f;--command:#2f7d6d;--run:#8a8a8f;--retired:#b9b7b1;--hl:#f2c94c}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#151517;--panel:#1f1f22;--ink:#ececef;
--muted:#9a9aa1;--line:#2e2e33;--link:#45454c;--language:#7b9be0;--topic:#b38ddb;--commands:#5fbfa9;
--lesson:#f0a05a;--fact:#6fb3ee;--finding:#de8cb6;--rule:#6cc795;--fix:#ee7b7b;--toolchain:#9cc560;--command:#5fbfa9;
--run:#8f8f96;--retired:#5c5b58}}
*{box-sizing:border-box}
body{margin:0;overflow-x:hidden;background:var(--bg);color:var(--ink);font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",
Roboto,sans-serif}
header{padding:12px 16px;border-bottom:1px solid var(--line);display:flex;flex-wrap:wrap;gap:8px 16px;
align-items:center}
h1{font-size:17px;margin:0}
.summary{color:var(--muted);font-size:13px;min-width:0;flex:1 1 300px}
#tools{display:none;gap:8px;flex-wrap:wrap;align-items:center;width:100%}
.js #tools{display:flex}
#search{flex:1 1 180px;min-width:0;padding:7px 10px;border:1px solid var(--line);border-radius:8px;
background:var(--panel);color:var(--ink);font:inherit}
select,button{max-width:100%;font:inherit;font-size:13px;padding:6px 10px;border:1px solid var(--line);border-radius:8px;
background:var(--panel);color:var(--ink);cursor:pointer}
.chip{display:inline-flex;align-items:center;gap:6px}
.chip[aria-pressed="false"]{opacity:.4}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block}
#stage{display:none;position:relative;height:calc(100vh - 110px);min-height:360px}
.js.graph #stage{display:block}
.js.graph #list{display:none}
canvas{width:100%;height:100%;display:block;touch-action:none}
#panel{position:absolute;top:12px;right:12px;width:340px;max-height:calc(100% - 24px);overflow:auto;
background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px;display:none;
box-shadow:0 6px 24px rgba(0,0,0,.12)}
#panel.open{display:block}
#panel h3{margin:0 0 6px;font-size:15px}
#panel .text{white-space:pre-wrap;margin:8px 0}
#panel dl{display:grid;grid-template-columns:auto 1fr;gap:2px 10px;font-size:13px;margin:8px 0}
#panel dt{color:var(--muted)}
#panel dd{margin:0}
#panel ul{padding-left:18px;margin:6px 0;font-size:13px}
#panel a{color:var(--fact)}
.close{float:right;border:none;background:none;font-size:18px;padding:0 4px;color:var(--muted)}
.hint{position:absolute;left:12px;bottom:10px;color:var(--muted);font-size:12px}
#list{padding:8px 16px 32px;max-width:900px}
#list h2{font-size:16px;margin:20px 0 6px}
#list details{margin:4px 0}
#list summary{cursor:pointer;color:var(--muted)}
#list li{margin:4px 0}
.tags{color:var(--muted);font-size:12px}
.nojs{color:var(--muted);font-size:13px}
.js .nojs{display:none}
@media (max-width:700px){header{display:block}.summary{display:block;margin:2px 0 8px}
.js #tools{display:grid;grid-template-columns:1fr 1fr auto}#search,#kinds{grid-column:1/-1}select{width:100%;min-width:0}
#panel{left:8px;right:8px;top:auto;bottom:8px;width:auto;max-height:55%}
#stage{height:calc(100vh - 150px)}}
</style>
</head>
<body>
<header>
<h1>Tinker's memory</h1>
<span class="summary">__SUMMARY__ · similar lessons by __SIMILAR__</span>
<p class="nojs">This is the plain list. Open the file in a browser to explore it as a graph.</p>
<div id="tools">
<input id="search" type="search" placeholder="Search what Tinker knows" aria-label="Search">
<select id="group" aria-label="Language or topic"><option value="">All languages and topics</option></select>
<select id="status" aria-label="Status"><option value="">Any status</option><option>proven</option>
<option>candidate</option><option>retired</option></select>
<button id="view" type="button">List</button>
<div id="kinds" style="display:flex;gap:6px;flex-wrap:wrap"></div>
</div>
</header>
<div id="stage"><canvas id="canvas"></canvas><aside id="panel" aria-live="polite"></aside>
<div class="hint">Drag to move · scroll or pinch to zoom · tap a node for its story</div></div>
<main id="list">
__LIST__
</main>
<script type="application/json" id="data">__DATA__</script>
<script>
(function(){
var data=JSON.parse(document.getElementById('data').textContent);
var canvas=document.getElementById('canvas');
if(!canvas.getContext)return;
document.body.classList.add('js','graph');
var ctx=canvas.getContext('2d'),css=getComputedStyle(document.documentElement);
function color(name){return css.getPropertyValue('--'+name).trim()||'#888';}
var HUBS={language:1,topic:1,commands:1};
var KINDS=['lesson','rule','fact','finding','fix','toolchain','command','run'];
var nodes=data.nodes,links=data.links,byId={};
var hubs=nodes.filter(function(n){return HUBS[n.kind];}),R=hubs.length>1?140*Math.sqrt(hubs.length):0;
hubs.forEach(function(h,i){var a=6.283*i/hubs.length;h.x=Math.cos(a)*R;h.y=Math.sin(a)*R;});
var hubOf={};hubs.forEach(function(h){hubOf[h.group]=h;});
nodes.forEach(function(n,i){byId[n.id]=n;n.links=[];n.vx=0;n.vy=0;
if(!HUBS[n.kind]){var h=hubOf[n.group]||{x:0,y:0},a=i*2.399,d=40+(i%7)*14;n.x=h.x+Math.cos(a)*d;n.y=h.y+Math.sin(a)*d;}
var helped=0,seen=0;n.details.forEach(function(d){if(d[0]==='helped')helped=+d[1]||0;if(d[0]==='seen')seen=+d[1]||0;});
n.r=HUBS[n.kind]?14:n.kind==='run'?5:Math.min(13,4+Math.sqrt(helped+seen)*2);});
links=links.filter(function(l){return byId[l.source]&&byId[l.target];});
links.forEach(function(l){l.s=byId[l.source];l.t=byId[l.target];l.s.links.push(l);l.t.links.push(l);});
var shownKinds={},search='',group='',status='',selected=null;
KINDS.concat(['language','topic','commands']).forEach(function(k){shownKinds[k]=true;});
var groups={};nodes.forEach(function(n){if(HUBS[n.kind])groups[n.group]=n.label;});
var sel=document.getElementById('group');
Object.keys(groups).sort().forEach(function(g){var o=document.createElement('option');o.value=g;
o.textContent=groups[g];sel.appendChild(o);});
var kindsBox=document.getElementById('kinds');
KINDS.forEach(function(k){if(!nodes.some(function(n){return n.kind===k;}))return;
var b=document.createElement('button');b.type='button';b.className='chip';b.setAttribute('aria-pressed','true');
b.innerHTML='<span class="dot" style="background:'+color(k)+'"></span>'+k;
b.onclick=function(){shownKinds[k]=!shownKinds[k];b.setAttribute('aria-pressed',String(shownKinds[k]));draw();};
kindsBox.appendChild(b);});
function visible(n){if(!shownKinds[n.kind])return false;if(status&&n.status!==status&&!HUBS[n.kind])return false;
if(group&&n.group!==group&&n.kind!=='run')return false;
if(group&&n.kind==='run')return n.links.some(function(l){var o=l.s===n?l.t:l.s;return o.group===group;});
return true;}
function matches(n){return !search||(n.text+' '+n.label).toLowerCase().indexOf(search)>=0;}
document.getElementById('search').oninput=function(e){search=e.target.value.trim().toLowerCase();draw();};
sel.onchange=function(e){group=e.target.value;draw();};
document.getElementById('status').onchange=function(e){status=e.target.value;draw();};
document.getElementById('view').onclick=function(e){var g=document.body.classList.toggle('graph');
e.target.textContent=g?'List':'Graph';if(g)resize();};
var view={x:0,y:0,k:1},W=0,H=0,dpr=window.devicePixelRatio||1;
function resize(){var r=canvas.getBoundingClientRect();W=r.width;H=r.height;canvas.width=W*dpr;canvas.height=H*dpr;draw();}
window.addEventListener('resize',resize);
var LEN={belongs_to:45,cites:70,became:60,similar:90},alpha=1;
function tick(){var i,j,a,b,dx,dy,d,f;
for(i=0;i<nodes.length;i++){a=nodes[i];for(j=i+1;j<nodes.length;j++){b=nodes[j];dx=b.x-a.x;dy=b.y-a.y;
d=dx*dx+dy*dy||0.01;if(d>62500)continue;f=(HUBS[a.kind]&&HUBS[b.kind]?6000:120)*alpha/d;
a.vx-=dx*f;a.vy-=dy*f;b.vx+=dx*f;b.vy+=dy*f;}}
links.forEach(function(l){dx=l.t.x-l.s.x;dy=l.t.y-l.s.y;d=Math.sqrt(dx*dx+dy*dy)||1;
f=(d-(LEN[l.kind]||120))/d*(l.kind==='belongs_to'?0.3:0.05)*alpha;var w=l.s.links.length/(l.s.links.length+l.t.links.length);
l.s.vx+=dx*f*(1-w);l.s.vy+=dy*f*(1-w);l.t.vx-=dx*f*w;l.t.vy-=dy*f*w;});
nodes.forEach(function(n){n.vx-=n.x*0.002*alpha;n.vy-=n.y*0.002*alpha;
if(n!==dragging){n.x+=n.vx;n.y+=n.vy;}n.vx*=0.6;n.vy*=0.6;});alpha*=0.985;}
function run(){if(alpha>0.02){tick();if(!touched)fit();draw();requestAnimationFrame(run);}}
var touched=false;
function fit(){var x0=1e9,y0=1e9,x1=-1e9,y1=-1e9;nodes.forEach(function(n){if(!visible(n))return;
x0=Math.min(x0,n.x);y0=Math.min(y0,n.y);x1=Math.max(x1,n.x);y1=Math.max(y1,n.y);});if(x0>x1)return;
view.k=Math.max(0.2,Math.min(2,Math.min((W-40)/(x1-x0||1),(H-40)/(y1-y0||1))));
view.x=-(x0+x1)/2*view.k;view.y=-(y0+y1)/2*view.k;}
function toScreen(n){return[(n.x*view.k+view.x)+W/2,(n.y*view.k+view.y)+H/2];}
function neighbours(n){var s={};if(!n)return s;s[n.id]=1;n.links.forEach(function(l){s[l.s.id]=1;s[l.t.id]=1;});return s;}
function draw(){ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,W,H);var near=neighbours(selected);
links.forEach(function(l){if(!visible(l.s)||!visible(l.t))return;var p=toScreen(l.s),q=toScreen(l.t);
var on=selected&&(l.s===selected||l.t===selected);ctx.globalAlpha=selected&&!on?0.15:on?0.9:0.5;
ctx.strokeStyle=l.kind==='failed_in'?color('fix'):l.kind==='helped_in'?color('toolchain'):color('link');
ctx.lineWidth=on?1.6:0.8;ctx.setLineDash(l.kind==='similar'?[4,4]:[]);ctx.beginPath();ctx.moveTo(p[0],p[1]);
ctx.lineTo(q[0],q[1]);ctx.stroke();});ctx.setLineDash([]);
nodes.forEach(function(n){if(!visible(n))return;var p=toScreen(n),r=n.r*Math.sqrt(view.k);
var dim=(selected&&!near[n.id])||!matches(n);ctx.globalAlpha=dim?0.15:1;
ctx.fillStyle=n.status==='retired'?color('retired'):color(n.kind);ctx.beginPath();ctx.arc(p[0],p[1],r,0,6.283);ctx.fill();
if(n.status==='proven'||n.status==='candidate'){ctx.strokeStyle=color('ink');ctx.lineWidth=n.status==='proven'?1.6:1;
ctx.setLineDash(n.status==='candidate'?[2,2]:[]);ctx.stroke();ctx.setLineDash([]);}
if(n===selected){ctx.strokeStyle=color('hl');ctx.lineWidth=3;ctx.stroke();}
if(HUBS[n.kind]||n===selected||(near[n.id]&&selected)||(search&&matches(n))||view.k>1.8){
ctx.fillStyle=color('ink');ctx.font=(HUBS[n.kind]?'600 13px':'12px')+' -apple-system,sans-serif';
ctx.fillText(n.label,p[0]+r+4,p[1]+4);}});ctx.globalAlpha=1;}
var panel=document.getElementById('panel');
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
var NAMES={belongs_to:'belongs to',cites:'cites',became:'became',similar:'similar to',taught_by:'taught by',
confirmed_in:'confirmed in',helped_in:'helped',failed_in:'failed in'};
function open(n){selected=n;if(!n){panel.className='';draw();return;}
var h='<button class="close" aria-label="Close">×</button><h3>'+esc(n.label)+'</h3><div class="tags">'+
esc(n.kind+(n.status?' · '+n.status:''))+'</div>';if(n.text!==n.label)h+='<div class="text">'+esc(n.text)+'</div>';
if(n.details.length){h+='<dl>';n.details.forEach(function(d){h+='<dt>'+esc(d[0])+'</dt><dd>'+esc(d[1])+'</dd>';});h+='</dl>';}
if(n.sources.length){h+='<b>Sources</b><ul>';n.sources.forEach(function(s){h+='<li><a href="'+esc(s)+
'" target="_blank" rel="noopener">'+esc(s)+'</a></li>';});h+='</ul>';}
if(n.links.length){h+='<b>Linked</b><ul>';n.links.slice(0,40).forEach(function(l){var o=l.s===n?l.t:l.s;
var how=l.s===n?NAMES[l.kind]||l.kind:(l.kind==='belongs_to'?'holds':'← '+(NAMES[l.kind]||l.kind));
h+='<li>'+esc(how)+' <a href="#" data-id="'+esc(o.id)+'">'+esc(o.label)+'</a></li>';});
if(n.links.length>40)h+='<li>… and '+(n.links.length-40)+' more</li>';h+='</ul>';}
panel.innerHTML=h;panel.className='open';panel.querySelector('.close').onclick=function(){open(null);};
panel.querySelectorAll('a[data-id]').forEach(function(a){a.onclick=function(e){e.preventDefault();
var o=byId[a.getAttribute('data-id')];view.x=-o.x*view.k;view.y=-o.y*view.k;open(o);};});draw();}
function nodeAt(x,y){var best=null,bd=1e9;nodes.forEach(function(n){if(!visible(n))return;var p=toScreen(n);
var d=Math.hypot(p[0]-x,p[1]-y),r=Math.max(10,n.r*Math.sqrt(view.k)+4);if(d<r&&d<bd){bd=d;best=n;}});return best;}
var pointers={},dragging=null,moved=false,start=null,pinch=null;
function pos(e){var r=canvas.getBoundingClientRect();return[e.clientX-r.left,e.clientY-r.top];}
canvas.addEventListener('pointerdown',function(e){touched=true;canvas.setPointerCapture(e.pointerId);var p=pos(e);
pointers[e.pointerId]=p;moved=false;var ids=Object.keys(pointers);
if(ids.length===2){var a=pointers[ids[0]],b=pointers[ids[1]];pinch={d:Math.hypot(a[0]-b[0],a[1]-b[1]),k:view.k};
dragging=null;return;}dragging=nodeAt(p[0],p[1]);start={p:p,x:view.x,y:view.y};});
canvas.addEventListener('pointermove',function(e){if(!pointers[e.pointerId])return;var p=pos(e);pointers[e.pointerId]=p;
var ids=Object.keys(pointers);if(ids.length===2&&pinch){var a=pointers[ids[0]],b=pointers[ids[1]];
view.k=Math.max(0.2,Math.min(5,pinch.k*Math.hypot(a[0]-b[0],a[1]-b[1])/pinch.d));draw();return;}
if(!start)return;if(Math.hypot(p[0]-start.p[0],p[1]-start.p[1])>4)moved=true;if(!moved)return;
if(dragging){dragging.x=(p[0]-W/2-view.x)/view.k;dragging.y=(p[1]-H/2-view.y)/view.k;if(alpha<0.1){alpha=0.1;run();}}
else{view.x=start.x+p[0]-start.p[0];view.y=start.y+p[1]-start.p[1];}draw();});
function up(e){var p=pos(e);if(!moved&&start&&Object.keys(pointers).length===1)open(nodeAt(p[0],p[1]));
delete pointers[e.pointerId];if(Object.keys(pointers).length<2)pinch=null;if(!Object.keys(pointers).length){dragging=null;start=null;}}
canvas.addEventListener('pointerup',up);canvas.addEventListener('pointercancel',up);
canvas.addEventListener('wheel',function(e){e.preventDefault();touched=true;var p=pos(e),k=Math.max(0.2,Math.min(5,view.k*Math.exp(-e.deltaY*0.0015)));
var cx=p[0]-W/2,cy=p[1]-H/2;view.x=cx-(cx-view.x)*k/view.k;view.y=cy-(cy-view.y)*k/view.k;view.k=k;draw();},{passive:false});
resize();run();
})();
</script>
</body>
</html>
"""
