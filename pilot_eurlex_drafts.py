"""Run a resumable German draft-summary pilot on explicitly selected CELEX identifiers."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import time

import summarize_eurlex_provisions as base

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "runs" / "eurlex_drafts_pilot_v1"
BUDGET = 28000  # characters per batch, well below the office model's context limit
RULES = """Schreibe einen verständlichen deutschen Erstentwurf. Nutze ausschließlich die
gelieferten Inhalte. Erhalte Adressat, Modalität, Bedingungen und Ausnahmen. Eine Befugnis
der Kommission ist keine Pflicht eines Herstellers. Gib 'may' als Befugnis wieder. WHO nur
bei ausdrücklich genanntem Akteur (auch Behörden). DUTY nur bei ausdrücklicher Verpflichtung
oder Verbot. Definitionen, Listen und Befugnisse als OTHER. Bei Änderungen nenne den
geänderten Rechtsakt, die Stelle und den Gegenstand, soweit belegt. Begriffe im Kontext:
declaration of performance = Leistungserklärung, making available on the market =
Bereitstellung auf dem Markt, placing on the market = Inverkehrbringen. Deutsche Prosa,
technische Kennungen und Eigennamen originalgetreu. Keine erfundenen Adressaten,
Abteilungen oder Anwendungsfälle. Jede Aussage als vollständiger Satz."""
CARD = """Gib headline, overview und bullets zurück. Nenne bei Anhängen die konkreten
Regelungsgegenstände. Erkläre bei Tabellen Zweck, Kategorien, Spalten, Einheiten und
wie die Werte zu lesen sind, soweit erkennbar. Keine erfundene Zuordnung bei fehlenden
Spaltenüberschriften. Beschreibe zusammengehörige Zeilen gemeinsam. Erfinde keine Pflicht
aus einer technischen Parameterliste. Jede Aufzählung erhält Quellenkennungen aus dem Input."""
ACT = """Erstelle eine Übersicht über die erfassten relevanten Vorschriften. Nenne zuerst
Gegenstand und belegte Auswirkungen für Unternehmen, danach wesentliche Behördenaufgaben.
Verwende den mitgelieferten Rechtsakttyp für den betreffenden Akt. Keine CELEX-Kennung in
der Prosa. Schreibe drei bis fünf vollständige, unterschiedliche deutsche Sätze."""
def obj(props):
    return dict(type="object", properties=props, required=list(props), additionalProperties=False)
STR = {"type":"string"}
def arr(items): return dict(type="array", items=items)
CARD_SCHEMA = obj(dict(headline=STR, overview=STR, bullets=arr(obj(dict(
    kind=dict(type="string", enum=["WHO","DUTY","DEADLINE","CONDITION","OTHER"]),
    text=STR, passages=arr(STR))))))
ACT_SCHEMA = obj(dict(sentences=arr(STR)))

def dump(x): return json.dumps(x, ensure_ascii=False, sort_keys=True)
def digest(x): return hashlib.sha256(dump(x).encode()).hexdigest()
def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(dump(data), encoding="utf-8")
    tmp.replace(path)

def table_key(item, p):
    loc = item["target"]
    if "table" not in (str(item.get("kind","")) + " " + loc).lower():
        return ("provision", str(item["target_id"]), loc)
    parent = re.split(r",\s*(?:unnumbered\s+)?table\b", loc, maxsplit=1, flags=re.I)[0]
    for field in ("parent_table_id","logical_table_id","table_id"):
        if isinstance(p.get(field), (str,int)) and str(p[field]).strip():
            return ("table", parent if parent != loc else "", str(p[field]))
    # Merge only explicitly identified row/part suffixes of the same table.
    m = re.fullmatch(r"(.+?\btable\s+[\w.-]+)(?:\s*[,/–:-]\s*|\s*\[)(?:row|part|chunk)\s+\d+\]?",loc,re.I)
    if m: return ("table", m[1])
    return ("table", str(item["target_id"]), loc)

def batches(items):
    batch, size = [], 0
    for item in items:
        n=len(dump(item))
        if size+n>BUDGET and batch:
            yield batch
            batch,size=[],0
        batch.append(item)
        size+=n
    if batch: yield batch

def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--celex", nargs="+", required=True)
    cli.add_argument("--count-only", action="store_true")
    args=cli.parse_args()
    ids=set(args.celex)
    if any(not re.fullmatch(r"3\d{4}[A-Z]\d{4}", a) for a in ids):
        cli.error("Expected standard CELEX act IDs.")
    items,_=base.collect([p.resolve() for p in base.DEFAULT_RUNS],base.DEFAULT_UNSURE)
    classification=base.read_json(base.DEFAULT_CLASSIFICATION/"results.json")
    groups=defaultdict(lambda:defaultdict(list))
    for i in items:
        a=i["celex"]
        if a in ids and i["level"] in base.KEPT and i["target"] in classification.get(a,{}):
            p=base.read_json(i["source"])["evidence_pack"]["provision"]
            groups[a][table_key(i,p)].append(i)
    for a in sorted(ids):
        print(a, len(groups[a]), "provision/table cards from", sum(map(len,groups[a].values())), "classified targets")
    if any(not groups[a] for a in ids):
        raise SystemExit("An ID has no eligible targets. Choose IDs listed in your classified run.")
    if args.count_only: return
    client=base.QwenClient.from_env(ROOT/".env", retries=1)
    ok,reason=base.reachable(client)
    if not ok: raise SystemExit(str(reason))
    total=sum(map(len,groups.values()))+len(ids)
    done=0
    started=time.monotonic()
    def progress(label):
        k=20*done//total
        print(f"[{'#'*k}{'-'*(20-k)}] {done}/{total} {100*done/total:.1f}% | {time.monotonic()-started:.0f}s | {label}",flush=True)
    def ask(mode, context, fragments):
        prompt=RULES+"\n"+(ACT if mode=="act" else CARD)
        payload=dict(context=context, material=fragments)
        key=digest([prompt,mode,payload])
        path=OUT/"cache"/(key+".json")
        if path.exists(): return base.read_json(path)
        if len(dump(payload))>100000:
            raise ValueError("Single source block too large; inspect table segmentation")
        for cap in (1800,3600):
            reply=client.chat([dict(role="system",content=prompt),dict(role="user",content=dump(payload))],
                thinking=False,stream=False,seed=42,sampling={"temperature":0},schema=ACT_SCHEMA if mode=="act" else CARD_SCHEMA,
                schema_name="pilot_"+mode,max_tokens=cap)
            if not reply.ok: raise RuntimeError(client.clean(reply.error or str(reply.error_kind)))
            data,error=reply.parse_json()
            if not error and isinstance(data,dict):
                if mode=="act":
                    ss=data.get("sentences")
                    if isinstance(ss,list) and ss and all(isinstance(v,str) and v.strip() for v in ss):
                        data["sentences"]=list(dict.fromkeys(ss))
                        break
                elif (all(isinstance(data.get(k),str) and data[k].strip() for k in ("headline","overview"))
                      and isinstance(data.get("bullets"),list) and all(isinstance(b,dict) and
                      isinstance(b.get("text"),str) and isinstance(b.get("passages"),list) for b in data["bullets"])):
                    break
        else: raise ValueError("Unreadable or incomplete structured answer after retry")
        write(path,data)
        return data

    def summarize(mode,ctx,rows):
        blocks=list(batches(rows))
        if not blocks: raise ValueError("No readable source material")
        while len(blocks)>1:
            drafts=[ask(mode,ctx,b) for b in blocks]
            smaller=list(batches(drafts))
            if len(smaller)>=len(blocks): raise ValueError("Intermediate summaries exceed batch budget")
            blocks=smaller
        return ask(mode,ctx,blocks[0])

    failures=[]
    for a in sorted(ids):
        cards=[]
        title=""
        ctx=dict(celex=a, act_type={"D":"Entscheidung/Beschluss (genaue Bezeichnung laut amtlichem Titel)",
                    "L":"Richtlinie","R":"Verordnung"}.get(a[5],"laut amtlichem Titel"))
        for key, members in groups[a].items():
            loc=members[0]["target"]
            progress(a+" "+loc)
            try:
                rows=[]
                refs={}
                for ix,item in enumerate(members,1):
                    pack=base.read_json(item["source"])["evidence_pack"]
                    title=pack["document"].get("title") or title
                    p=pack["provision"]
                    # Use the original packer for table headers, rows and source notes.
                    parts=base.split_target(p,base.TARGET_CHARS) or [p]
                    for j,part in enumerate(parts,1):
                        packed,passages,_=base.request_pack(pack,part,None,None)
                        target=packed.get("target",{})
                        mapping={m:f"S{ix}_{j}_{m}" for m in passages}
                        # Structured targets may have rows/cells without a text field.
                        if not passages and not any(target.get(k) for k in ("text","rows","cells","table","tables")):
                            raise ValueError("Target contains no readable source passages or table data")
                        refs.update({mapping[m]:v for m,v in passages.items()})
                        rows.append(dict(locator=item["target"], target=target, passage_ids=mapping, citation_instruction="Use the S-prefixed identifier for every passage citation.",
                                         source_notes=packed.get("source_notes",[])))
                card=summarize("card",dict(**ctx,title=title,target=loc),rows)
                route=[dict(target=i["target"],id=i["target_id"],level=i["level"],
                            classification=classification[a][i["target"]]) for i in members]
                warnings=[]
                if any(mark not in refs for b in card["bullets"] for mark in b["passages"]):
                    warnings.append("Check cited passage identifiers")
                if any(not (i["classification"].get("departments") or i["classification"].get("secondary_departments")) for i in route):
                    warnings.append("Department routing incomplete")
                record=dict(celex=a,target=loc,members=route,summary=card,source_passages=refs,
                            status="draft",warnings=warnings)
                write(OUT/a/"cards"/(digest(key)+".json"),record)
                cards.append(dict(locator=loc,summary=card))
            except (ValueError,KeyError,TypeError) as exc:
                failures.append(dict(celex=a,target=loc,error=str(exc)))
            done+=1
        progress(a+" act summary")
        if cards:
            try:
                act=summarize("act",dict(**ctx,title=title,scope="ausgewählte relevante Vorschriften"),cards)
                write(OUT/a/"act.json",dict(**ctx,**act,covered=[c["locator"] for c in cards],
                      planned=len(groups[a]),status="draft",partial=len(cards)!=len(groups[a])))
            except (ValueError,KeyError,TypeError) as exc:
                failures.append(dict(celex=a,target="act",error=str(exc)))
        else: failures.append(dict(celex=a,target="act",error="No provision drafts available"))
        done+=1
    write(OUT/("run_"+digest(sorted(ids))[:12]+".json"),dict(acts=sorted(ids),planned=total,failures=failures))
    progress(f"Finished | {len(failures)} failed | {OUT}")
    return int(bool(failures))

if __name__=="__main__":
    raise SystemExit(main())
