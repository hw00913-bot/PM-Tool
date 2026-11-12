"""Finite, read-only lexical source-chain proofs; never a JavaScript evaluator.

Accepted evidence shows source-level conditional reachability only. It does not
establish browser execution, production availability, permissions or G3 approval.
Unrecognized syntax is deliberately a diagnostic, never an exclusion decision.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from html.parser import HTMLParser
import hashlib
import re

IDENT = re.compile(r'[A-Za-z_$][A-Za-z0-9_$]*')
STATIC_KEY = re.compile(r'[A-Za-z0-9_$-]+')
EVENTS = {'click', 'change', 'submit', 'input', 'keydown', 'keyup'}
RAW_TAGS = {'script', 'style', 'textarea', 'title', 'template', 'noscript'}
CONTROL_TAGS = {'button', 'a', 'input', 'select', 'textarea', 'form', 'div', 'span', 'label', 'td', 'tr'}


class Unsupported(ValueError):
    pass


@dataclass(eq=False)
class Token:
    kind: str
    value: str
    start: int
    end: int
    parts: list = field(default_factory=list)
    parent_template: object = None
    parent_expression: object = None


def lex(source, offset=0, parent_template=None, parent_expression=None):
    """Small lexer for provenance, with opaque strings/comments/regexp literals.

    Template expressions are lexed separately and remain attached to their
    template; text resembling code inside a literal is never an actual call.
    No expressions are evaluated and no imports/source functions are executed.
    """
    tokens=[];i=0
    while i<len(source):
        c=source[i]
        if c.isspace():i+=1;continue
        if source.startswith('//',i):
            j=source.find('\n',i);i=len(source) if j<0 else j;continue
        if source.startswith('/*',i):
            j=source.find('*/',i+2)
            if j<0:raise Unsupported('Unclosed comment')
            i=j+2;continue
        start=i
        if c in "'\"":
            i+=1
            while i<len(source) and source[i]!=c:
                if source[i]=='\\':i+=2
                else:i+=1
            if i>=len(source):raise Unsupported('Unclosed quoted literal')
            i+=1;token=Token('string',source[start:i],offset+start,offset+i)
        elif c=='`':
            token=Token('template','',offset+start,0);i+=1;part=i
            while i<len(source):
                if source[i]=='\\':i+=2;continue
                if source[i]=='`':
                    token.parts.append(('text',source[part:i]));i+=1;break
                if source.startswith('${',i):
                    token.parts.append(('text',source[part:i]));first=i+2
                    # Lex the expression recursively, stopping at its matching }.
                    expr,end=lex_expression(source,first,offset,token)
                    token.parts.append(('expression',expr));i=end;part=i;continue
                i+=1
            else:raise Unsupported('Unclosed template literal')
            token.value=source[start:i];token.end=offset+i
        elif c=='/':
            previous=tokens[-1].value if tokens else None
            # Regexp may follow expression-start punctuation/keywords. Division
            # is a punctuation token; neither form is accepted as a proof edge.
            regex_start=previous is None or previous in {'(', '[', '{', '=', ':', ',', ';', '!', '?', 'return', '=>', '||', '&&'}
            if regex_start:
                i+=1;in_class=False
                while i<len(source):
                    if source[i]=='\\':i+=2;continue
                    if source[i]=='[':in_class=True
                    elif source[i]==']':in_class=False
                    elif source[i]=='/' and not in_class:break
                    elif source[i] in '\r\n':raise Unsupported('Unclosed regexp literal')
                    i+=1
                if i>=len(source):raise Unsupported('Unclosed regexp literal')
                i+=1
                while i<len(source) and source[i].isalpha():i+=1
                token=Token('opaque-regexp',source[start:i],offset+start,offset+i)
            else:i+=1;token=Token('punct','/',offset+start,offset+i)
        elif m:=IDENT.match(source,i):
            i=m.end();token=Token('id',m.group(),offset+start,offset+i)
        elif c.isdigit():
            i+=1
            while i<len(source) and (source[i].isalnum() or source[i] in '._'):i+=1
            token=Token('number',source[start:i],offset+start,offset+i)
        else:
            op=next((op for op in ('===','!==','...','=>','?.','==','!=','&&','||','??','<=','>=','++','--','+=','-=','**') if source.startswith(op,i)),c)
            i+=len(op);token=Token('punct',op,offset+start,offset+i)
        token.parent_template=parent_template;token.parent_expression=parent_expression
        tokens.append(token)
    return tokens


def lex_expression(source,start,offset,template):
    """Find interpolation boundary lexically, respecting nested literals."""
    i=start;depth=0
    while i<len(source):
        if source.startswith('//',i):
            j=source.find('\n',i);i=len(source) if j<0 else j;continue
        if source.startswith('/*',i):
            j=source.find('*/',i+2)
            if j<0:raise Unsupported('Unclosed expression comment')
            i=j+2;continue
        if source[i] in "'\"`":
            # Parse one literal by the same lexer without scanning later text.
            end=literal_end(source,i)
            i=end;continue
        if source[i]=='{':depth+=1
        elif source[i]=='}':
            if depth==0:
                expr=lex(source[start:i],offset+start,template)
                for t in expr:t.parent_expression=expr
                return expr,i+1
            depth-=1
        i+=1
    raise Unsupported('Unclosed template interpolation')


def literal_end(source,start):
    quote=source[start];i=start+1
    while i<len(source):
        if source[i]=='\\':i+=2;continue
        if quote=='`' and source.startswith('${',i):
            _,i=lex_expression(source,i+2,0,None);continue
        if source[i]==quote:return i+1
        i+=1
    raise Unsupported('Unclosed nested literal')


def literal(token):
    if token.kind!='string' or '\\' in token.value:return None
    return token.value[1:-1]


def matching(tokens):
    result={};stack=[];pairs={')':'(',']':'[','}':'{'}
    for i,t in enumerate(tokens):
        if t.value in {'(','[','{'}:stack.append((t.value,i))
        elif t.value in pairs:
            if not stack or stack[-1][0]!=pairs[t.value]:raise Unsupported('Unbalanced source delimiters')
            _,j=stack.pop();result[i]=j;result[j]=i
    if stack:raise Unsupported('Unbalanced source delimiters')
    return result


def split_tokens(tokens,separator=','):
    matches=matching(tokens);result=[];start=0;i=0
    while i<len(tokens):
        if tokens[i].value in {'(','[','{'}:i=matches[i]+1;continue
        if tokens[i].value==separator:result.append(tokens[start:i]);start=i+1
        i+=1
    result.append(tokens[start:]);return result


def path_at(tokens,index=0,allow_factory=True,partial=False):
    if index>=len(tokens) or tokens[index].kind!='id':return None
    parts=[tokens[index].value];i=index+1
    if allow_factory and i+1<len(tokens) and tokens[i].value=='(' and tokens[i+1].value==')':
        # Only a bare zero-argument factory with a subsequent member.
        if i+2<len(tokens) and tokens[i+2].value in {'.','['}:parts[0]+='()';i+=2
    while i<len(tokens):
        if i+1<len(tokens) and tokens[i].value=='.' and tokens[i+1].kind=='id':parts.append(tokens[i+1].value);i+=2
        elif i+2<len(tokens) and tokens[i].value=='[' and tokens[i+2].value==']':
            key=literal(tokens[i+1])
            if key is None or not STATIC_KEY.fullmatch(key):return (tuple(parts),i) if partial else None
            parts.append(key);i+=3
        else:break
    return tuple(parts),i


def normalized_callee(value):
    if not isinstance(value,str):return None
    try:tokens=lex(value)
    except Unsupported:return None
    result=path_at(tokens)
    return result[0] if result and result[1]==len(tokens) else None


def iter_lists(tokens):
    yield tokens
    for t in tokens:
        if t.kind=='template':
            for kind,part in t.parts:
                if kind=='expression':yield from iter_lists(part)


@dataclass
class Call:
    path: tuple
    tokens: list
    index: int
    open_index: int
    end_index: int
    args: list
    @property
    def start(self):return self.tokens[self.index].start
    @property
    def end(self):return self.tokens[self.end_index].end


def calls(tokens):
    m=matching(tokens);result=[]
    for i,t in enumerate(tokens):
        if t.kind!='id' or (i and (tokens[i-1].value in {'.','?.','function','new'} or tokens[i-1].kind=='id' and tokens[i-1].value=='function')):continue
        p=path_at(tokens,i)
        if not p:continue
        path,j=p
        if j>=len(tokens) or tokens[j].value!='(':continue
        end=m[j]
        # Object/class methods and all function declarations are not calls.
        if end+1<len(tokens) and tokens[end+1].value=='{':continue
        result.append(Call(path,tokens,i,j,end,split_tokens(tokens[j+1:end])))
    return result


@dataclass(eq=False)
class Function:
    symbol: str|None
    start: int
    end: int
    body_start: int
    body_end: int
    parent: object=None
    is_declaration: bool=False


class Model:
    def __init__(self,path):
        self.path=path;self.source=path.read_text(encoding='utf-8');self.tokens=lex(self.source)
        self.lists=list(iter_lists(self.tokens));self.calls=[];self.functions=[]
        for ts in self.lists:
            ms=matching(ts)
            self.calls.extend(calls(ts))
            for i,t in enumerate(ts):
                if t.value!='function':continue
                j=i+1;symbol=None
                if j<len(ts) and ts[j].kind=='id':symbol=ts[j].value;j+=1
                if j>=len(ts) or ts[j].value!='(':continue
                close=ms[j]
                if close+1>=len(ts) or ts[close+1].value!='{':continue
                end=ms[close+1]
                prior=ts[i-1].value if i else None
                before_async=ts[i-2].value if i>1 and prior=='async' else None
                is_declaration=bool(symbol) and (prior in {None,'{','}',';'} or prior=='async' and before_async in {None,'{','}',';'})
                self.functions.append(Function(symbol,t.start,ts[end].end,ts[close+1].start,ts[end].end,is_declaration=is_declaration))
        for ts in self.lists:
            ms=matching(ts)
            known={f.body_start for f in self.functions}
            for i,t in enumerate(ts):
                body=None
                if t.value=='=>' and i+1<len(ts) and ts[i+1].value=='{':body=i+1
                elif t.kind=='id' and t.value not in {'if','for','while','switch','catch','with','function'} and i+1<len(ts) and ts[i+1].value=='(':
                    close=ms[i+1]
                    if close+1<len(ts) and ts[close+1].value=='{':body=close+1
                if t.value=='=>' and i+1<len(ts) and ts[i+1].value!='{':
                    # An expression arrow is a deferred callback scope even
                    # when evidence quotes only its following source line.
                    # Scan its expression as opaque balanced groups, not as
                    # a call executed directly by the enclosing business fn.
                    end=i+1
                    while end<len(ts) and ts[end].value not in {',',';',')',']','}'}:
                        if ts[end].value in {'(','[','{'}:end=ms[end]+1
                        else:end+=1
                    if end>i+1:self.functions.append(Function(None,t.start,ts[end-1].end,t.end,ts[end-1].end))
                if body is not None and ts[body].start not in known:
                    end=ms[body];self.functions.append(Function(None,t.start,ts[end].end,ts[body].start,ts[end].end));known.add(ts[body].start)
        for f in self.functions:f.parent=self.enclosing_function(f.start,exclude=f)
    def enclosing_function(self,pos,exclude=None):
        items=[f for f in self.functions if f is not exclude and f.body_start<pos<f.body_end]
        return min(items,key=lambda f:f.end-f.start) if items else None
    def declaration(self,symbol,parent=None):
        rows=[f for f in self.functions if f.symbol==symbol and f.parent is parent and f.is_declaration]
        return rows[0] if len(rows)==1 else None
    def scope(self,pos):return self.enclosing_function(pos)
    def variable_bindings(self):
        for ts in self.lists:
            ms=matching(ts)
            for i,t in enumerate(ts):
                if t.value not in {'const','let','var'}:continue
                j=i+1
                while j<len(ts):
                    first=j
                    while j<len(ts) and ts[j].value not in {'=',',',';'}:
                        if ts[j].value in {'(','[','{'}:j=ms[j]+1
                        else:j+=1
                    for token in ts[first:j]:
                        if token.kind=='id':yield token.value,t.start,self.scope(t.start)
                    if j>=len(ts) or ts[j].value==';':break
                    if ts[j].value=='=':
                        j+=1
                        while j<len(ts) and ts[j].value not in {',',';','}'}:
                            if ts[j].value in {'(','[','{'}:j=ms[j]+1
                            else:j+=1
                    if j>=len(ts) or ts[j].value!=',':break
                    j+=1

    def token_in(self,position):
        return any(t.start==position for ts in self.lists for t in ts)


@dataclass
class Evidence:
    model: Model
    start: int
    end: int
    record: dict
    def contains(self,node):return self.start<=node.start and node.end<=self.end


class HandlerParser(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.handlers=[];self.raw=[]
    def handle_starttag(self,tag,attrs):
        if self.raw:return
        if tag in CONTROL_TAGS:
            for k,v in attrs:
                if k.startswith('on') and k[2:] in EVENTS and isinstance(v,str):self.handlers.append((k[2:],v))
        if tag in RAW_TAGS:self.raw.append(tag)
    def handle_endtag(self,tag):
        if self.raw and self.raw[-1]==tag:self.raw.pop()


class TableHTMLContext(HTMLParser):
    """Only active HTML data slots; never attributes, comments or inert text."""
    VOID={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
    def __init__(self,markers):
        super().__init__(convert_charrefs=True);self.stack=[];self.markers=markers;self.active=set();self.handlers=[]
    def handle_starttag(self,tag,attrs):
        values=dict(attrs);style=values.get('style') or ''
        blocked=any(x[1] for x in self.stack) or tag in RAW_TAGS or 'hidden' in values or 'inert' in values or bool(re.search(r'(?:display\s*:\s*none|visibility\s*:\s*hidden|content-visibility\s*:\s*hidden)',style,re.I))
        if not blocked and tag in CONTROL_TAGS:
            self.handlers.extend((k[2:],v) for k,v in attrs if k.startswith('on') and k[2:] in EVENTS and isinstance(v,str))
        if tag not in self.VOID:self.stack.append((tag,blocked))
    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        if tag not in self.VOID:self.handle_endtag(tag)
    def handle_endtag(self,tag):
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag:del self.stack[i:];break
    def handle_data(self,data):
        if not any(x[1] for x in self.stack):self.active.update(m for m in self.markers if m in data)


class P3BalancedHTML(TableHTMLContext):
    """Literal template fragments cannot change a sibling's HTML context."""
    def __init__(self):super().__init__({});self.valid=True
    def handle_endtag(self,tag):
        if not self.stack or self.stack[-1][0]!=tag:self.valid=False
        super().handle_endtag(tag)


def template_markup(token):
    text=[];expressions={};index=0
    for kind,part in token.parts:
        if kind=='text':text.append(part)
        else:
            marker='__SOURCE_CHAIN_EXPR_'+str(index)+'__';index+=1
            text.append(marker);expressions[marker]=part
    return ''.join(text),expressions


def exact_handler(handler,callee,expressions,helper):
    # One invocation plus the standard form-submit return-false suffix only.
    h=handler.strip();h=re.sub(r';\s*return\s+false\s*;?\s*$','',h).strip().removesuffix(';').strip()
    if h in expressions:
        ts=expressions[h];cs=calls(ts)
        if not helper or len(cs)!=1:return False
        c=cs[0]
        if c.index!=0 or c.end_index!=len(ts)-1 or c.path!=(helper['name'],):return False
        if not c.args or len(c.args[0])!=1 or literal(c.args[0][0])!=helper['method']:return False
        return helper['owner']+(helper['method'],)==callee
    try:ts=lex(h)
    except Unsupported:return False
    if ts and ts[0].value=='return':ts=ts[1:]
    cs=calls(ts)
    return len(cs)==1 and cs[0].index==0 and cs[0].end_index==len(ts)-1 and cs[0].path==callee


class ChainReview:
    def __init__(self,review,proof,record,scope_review,candidate_path):
        self.review=review;self.proof=proof;self.record=record;self.scope_review=scope_review;self.candidate_path=candidate_path
        self.models={};self.errors=[]
        self.allowed_refs=set(record.get('source_refs',[]))|set(scope_review.get('source_refs',[]))|set(proof.get('source_refs',[]))
    def evidence(self,data):
        if not isinstance(data,dict):raise Unsupported('Missing structured source-chain evidence')
        problems=self.review.references(data)
        if not set(data.get('source_refs',[])).issubset(self.allowed_refs):problems.append('Source-chain evidence has unrelated registered source references')
        path=self.review.path(data.get('file',''))
        if path is None:raise Unsupported('Source-chain evidence source file is missing')
        if data.get('source_sha256')!=hashlib.sha256(path.read_bytes()).hexdigest():problems.append('Source-chain evidence SHA differs')
        problems.extend(self.review.evidence_source_chain_errors(data,{'source_refs':data.get('source_refs',[])}))
        lines=path.read_text(encoding='utf-8').splitlines(keepends=True);first,last=data.get('line_start'),data.get('line_end')
        if type(first) is not int or type(last) is not int or not 1<=first<=last<=len(lines):raise Unsupported('Invalid source-chain evidence line range')
        quote='\n'.join(line.rstrip('\r\n') for line in lines[first-1:last])
        if data.get('source_quote')!=quote:problems.append('Source-chain quote differs from exact original lines')
        if problems:raise Unsupported('; '.join(problems))
        if path not in self.models:self.models[path]=Model(path)
        return Evidence(self.models[path],sum(map(len,lines[:first-1])),sum(map(len,lines[:last])),data)
    def owner_export(self,e,callee,symbol):
        expected=normalized_callee(callee)
        if not expected or len(expected)<2 or not IDENT.fullmatch(symbol or ''):raise Unsupported('Invalid static owner export target')
        found=[];assignments=0
        for ts in e.model.lists:
            ms=matching(ts)
            for i in range(len(ts)):
                if i and ts[i-1].value in {'.','?.'}:continue
                p=path_at(ts,i,False)
                if not p:continue
                owner,j=p
                if owner!=expected[:-1] or j>=len(ts) or ts[j].value!='=':continue
                assignments+=1
                k=j+1
                if k+3<len(ts) and [x.value for x in ts[k:k+4]]==['Object','.','freeze','(']:
                    self.no_shadow(e.model,'Object',None,[ts[k].start],include_parent=True)
                    k+=4
                if k>=len(ts) or ts[k].value!='{':continue
                end=ms[k]
                if not(e.start<=ts[i].start and ts[end].end<=e.end):continue
                props={}
                for part in split_tokens(ts[k+1:end]):
                    if not part:continue
                    key=part[0].value if part[0].kind=='id' else literal(part[0])
                    if key is None or part[0].value in {'...','['} or len(part)>1 and part[0].value in {'get','set','async'} and part[1].kind=='id':raise Unsupported('Dynamic/computed/spread/accessor export properties cannot establish a static owner binding')
                    if key in props:raise Unsupported('Duplicate static owner property')
                    if len(part)==1 and part[0].kind=='id':props[key]=part[0].value
                    elif len(part)==3 and part[1].value==':' and part[2].kind=='id':props[key]=part[2].value
                    elif len(part)>1 and part[1].value in {':','('}:props[key]=None
                    else:raise Unsupported('Unsupported static export property grammar')
                if props.get(expected[-1])!=symbol:continue
                self.no_shadow(e.model,expected[0],None,[ts[i].start],include_parent=True)
                parent=e.model.scope(ts[i].start);fn=e.model.declaration(symbol,parent)
                if fn is None or fn.parent is not parent:raise Unsupported('Static owner export lacks same lexical-scope unique named implementation')
                found.append((fn,parent,props,ts[i].start))
        if len(found)!=1 or assignments!=1:raise Unsupported('No unique exact static owner export/property binding')
        for ts in e.model.lists:
            for i,t in enumerate(ts):
                if t.kind!='id' or i and ts[i-1].value in {'.','?.'}:continue
                path=path_at(ts,i,False)
                partial=path_at(ts,i,False,partial=True)
                if partial:
                    prefix,at=partial
                    if prefix==expected[:len(prefix)] and at<len(ts) and ts[at].value=='[':
                        ms=matching(ts);close=ms[at]
                        if close+1<len(ts) and ts[close+1].value in {'=','+=','-=','++','--'} or i>0 and ts[i-1].value=='delete':raise Unsupported('Dynamic owner property mutation leaves the exact operation binding unproven')
                if path:
                    actual,j=path
                    mutating=j<len(ts) and ts[j].value in {'=','+=','-=','++','--'} or i>0 and ts[i-1].value=='delete'
                    if actual==expected and mutating:raise Unsupported('Static owner target property is rewritten or deleted in the original source')
                if t.value==symbol and i+1<len(ts) and ts[i+1].value in {'=','+=','-=','++','--'}:raise Unsupported('Original implementation symbol is rebound in the source')
        return found[0]
    def alias(self,e,alias,parent):
        found=[]
        for ts in e.model.lists:
            for i,t in enumerate(ts):
                if t.value!='const':continue
                j=i+1;ms=matching(ts)
                while j<len(ts):
                    if ts[j].kind!='id' or j+1>=len(ts) or ts[j+1].value!='=':break
                    start=j;j+=2;k=j
                    while k<len(ts) and ts[k].value not in {',',';'}:
                        if ts[k].value in {'(','[','{'}:k=ms[k]+1
                        else:k+=1
                    rhs=ts[j:k];p=path_at(rhs,0,False)
                    if ts[start].value==alias and p and p[1]==len(rhs) and p[0] in {('PlatformUI',),('window','PlatformUI')}:
                        if e.start<=t.start and ts[k-1].end<=e.end and e.model.scope(t.start) is parent:found.append(t)
                    if k>=len(ts) or ts[k].value!=',':break
                    j=k+1
        if len(found)!=1:raise Unsupported('UI sink alias needs unique same-scope const alias to PlatformUI')
        # A nearer local declaration can shadow an outer UI alias.
        for ts in e.model.lists:
            for i,t in enumerate(ts[:-1]):
                if t.value in {'const','let','var'} and ts[i+1].value==alias and t is not found[0]:
                    if e.model.scope(t.start) is not parent:raise Unsupported('UI sink alias has an unsupported shadowing declaration')
        return alias
    def helper(self,e,call):
        if not isinstance(call,dict) or not IDENT.fullmatch(call.get('name','')) or not IDENT.fullmatch(call.get('literal_method','')):raise Unsupported('Handler helper needs fixed literal method binding')
        name,method=call['name'],call['literal_method'];found=[]
        for ts in e.model.lists:
            ms=matching(ts)
            for i in range(len(ts)-8):
                if [t.value for t in ts[i:i+2]]!=['const',name] or ts[i+2].value!='=' or ts[i+3].value!='(':continue
                close=ms[i+3];params=ts[i+4:close]
                if len(params)!=4 or params[0].kind!='id' or [t.value for t in params[1:3]]!=[',','...'] or params[3].kind!='id':continue
                if close+2>=len(ts) or ts[close+1].value!='=>':continue
                template=ts[close+2]
                if template.kind!='template' or len(template.parts)!=5:continue
                parts=template.parts
                if [x[0] for x in parts]!=['text','expression','text','expression','text']:continue
                if len(parts[1][1])!=1 or parts[1][1][0].value!=params[0].value or parts[2][1]!='(' or parts[4][1]!=')':continue
                owner=normalized_callee(parts[0][1].removesuffix('.'))
                if not owner or not parts[0][1].endswith('.'):continue
                # Arguments may only be escaped/joined by the fixed map helper;
                # they cannot replace the owner or method of this event binding.
                argts=parts[3][1]
                expected_prefix=[params[3].value,'.','map','(']
                if [t.value for t in argts[:4]]!=expected_prefix or len(argts)<10:continue
                if not (e.start<=ts[i].start and template.end<=e.end):continue
                found.append({'name':name,'method':method,'owner':owner,'parent':e.model.scope(ts[i].start),'model':e.model})
        if len(found)!=1:raise Unsupported('Handler helper is not supported fixed-owner template grammar')
        return found[0]
    # P1 is opt-in.  These bounded helpers do not change the old UI sinks,
    # owner matching, call links, or implicit-global rejection rules.
    def p1_function(self,e,symbol,parent=None):
        fn=e.model.declaration(symbol,parent)
        if fn is None or not e.contains(fn):raise Unsupported('Table provenance needs the complete unique named implementation: '+str(symbol))
        for ts in e.model.lists:
            hit=next((i for i,t in enumerate(ts) if t.start==fn.start),None)
            if hit is None:continue
            ms=matching(ts);j=hit+2
            if j>=len(ts) or ts[j].value!='(':raise Unsupported('Unsupported table-provenance function parameters')
            params=split_tokens(ts[j+1:ms[j]])
            if any(len(p)!=1 or p[0].kind!='id' for p in params if p):raise Unsupported('Table provenance accepts plain named parameters only')
            body=ms[j]+1
            return fn,[p[0].value for p in params if p],ts[body+1:ms[body]]
        raise Unsupported('Missing exact function tokens')

    def p1_props(self,ts):
        if not ts or ts[0].value!='{' or matching(ts).get(0)!=len(ts)-1:raise Unsupported('Table provenance needs a literal object')
        props={}
        for part in split_tokens(ts[1:-1]):
            if not part:continue
            key=part[0].value if part[0].kind=='id' else literal(part[0])
            if key is None or key in props or len(part)<3 or part[1].value!=':':raise Unsupported('Computed/spread/duplicate table provenance property')
            props[key]=part[2:]
        return props

    def p1_literal(self,ts):
        return literal(ts[0]) if len(ts)==1 else None

    def p1_returns(self,model,fn,ts):
        return [ts[i+1] for i,t in enumerate(ts[:-1]) if t.value=='return' and ts[i+1].kind=='template' and model.enclosing_function(t.start) is fn]

    def p1_active_part(self,template,part):
        html,expressions=template_markup(template);parser=TableHTMLContext(expressions);parser.feed(html);parser.close()
        return any(value is part and marker in parser.active for marker,value in expressions.items())

    def p1_returned_call(self,model,fn,templates,path):
        self.p1_single_top_return(model,fn,templates)
        found=[]
        for c in model.calls:
            if c.path!=path or model.enclosing_function(c.start) is not fn:continue
            if any(c.tokens is part and c.index==0 and c.end_index==len(part)-1 and self.p1_active_part(t,part) for t in templates for kind,part in t.parts if kind=='expression'):found.append(c)
        if len(found)!=1:raise Unsupported('Table provenance needs one exact call whose result is inserted in a returned template')
        return found[0]

    def p1_single_top_return(self,model,fn,templates):
        returns=[t for ts in model.lists for t in ts if t.value=='return' and fn.body_start<t.start<fn.body_end and model.enclosing_function(t.start)is fn]
        if len(returns)!=1 or len(templates)!=1:raise Unsupported('Bounded table wrapper/delegate needs one original returned template')
        rt=returns[0]
        for ts in model.lists:
            ms=matching(ts);opening=next((i for i,t in enumerate(ts)if t.start==fn.body_start),None)
            if opening is None:continue
            i=opening+1;statement=[]
            while i<ms[opening]:
                t=ts[i]
                if t is rt:
                    if any(x.value in {'if','else','while','for','switch','?'} for x in statement):raise Unsupported('Returned table result is in an unsupported conditional statement')
                    return
                if t.value==';':statement=[];i+=1;continue
                statement.append(t)
                if t.value in {'(','[','{'}:i=ms[i]+1
                else:i+=1
        raise Unsupported('Returned table result is not the direct function-body result')

    def p1_identity_argument(self,handler,callee,expressions,params,key,index):
        h=handler.strip().removesuffix(';').strip()
        try:cs=calls(lex(h))
        except Unsupported:return False
        if len(cs)!=1 or cs[0].path!=callee or len(cs[0].args)<=index:return False
        arg=cs[0].args[index]
        if len(arg)!=1:return False
        marker=literal(arg[0]) if arg[0].kind=='string' else arg[0].value
        expression=expressions.get(marker)
        if expression is None:return False
        values=[x.value for x in expression]
        if params and values==[params[0]]:return True
        if len(params)<2:return False
        if values==[params[1],'.',key]:return True
        return len(expression)==4 and values[0:2]==[params[1],'['] and literal(expression[2])==key and values[3]==']'

    def p1_collection_stable(self,model,name,array_start,parent):
        mutators={'push','pop','shift','unshift','splice','sort','reverse','fill','copyWithin','clear','delete','set'}
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts[:-1]):
                if t.value!=name or i and ts[i-1].value in {'.','?.'}:continue
                if ts[i+1].value=='=':
                    if not(i and ts[i-1].value=='const' and i+2<len(ts) and ts[i+2].start==array_start):raise Unsupported('Registered route collection is replaced')
                j=i+1
                while j<len(ts) and ts[j].value in {'.','['}:
                    j=ms[j]+1 if ts[j].value=='[' else j+2
                if j<len(ts) and ts[j].value in {'=','+=','-=','++','--'} and j!=i+1:raise Unsupported('Registered route collection/property is mutated')
                if i and ts[i-1].value=='delete':raise Unsupported('Registered route collection is deleted')
        if any(c.path[:1]==(name,) and len(c.path)>1 and c.path[1] in mutators for c in model.calls):raise Unsupported('Registered route collection has a destructive call')

    def p1_navigation_stable(self,model,navigation,start,sink):
        roots={'page','opts','route'}
        if any([t.value for t in ts[i:i+3]]==['const','resolved','='] for ts in model.lists for i in range(len(ts)-2) if navigation.body_start<ts[i].start<navigation.body_end):roots.add('resolved')
        for name in roots|{'content'}:
            declarations=[t for ts in model.lists for i,t in enumerate(ts[:-2]) if t.value in {'const','let','var'} and ts[i+1].value==name and ts[i+2].value=='=' and navigation.body_start<t.start<navigation.body_end]
            if len(declarations)!=1:raise Unsupported('Navigation binding is shadowed or duplicated: '+name)
        for c in model.calls:
            if not(start<c.start<sink.start and navigation.body_start<c.start<navigation.body_end):continue
            if c.path in {('Object','assign'),('Object','defineProperty'),('Object','defineProperties'),('Reflect','set'),('Reflect','deleteProperty')} and c.args and [t.value for t in c.args[0]] in [[x] for x in roots]:
                if c.path!=('Object','assign') or [t.value for t in c.args[0]]!=['opts'] or len(c.args)<2:raise Unsupported('Navigation object is mutated by an unproven call')
                last=self.p1_props(c.args[-1])
                if [t.value for t in last.get('view',[])]!=['route','.','view']:raise Unsupported('Navigation Object.assign does not end in exact route.view')
                self.no_shadow(model,'Object',None,[c.start],include_parent=True)
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts[:-1]):
                if not(start<t.start<sink.start and navigation.body_start<t.start<navigation.body_end) or t.value not in roots|{'content'} or i and ts[i-1].value in {'.','?.'}:continue
                j=i+1
                while j<len(ts) and ts[j].value in {'.','['}:
                    j=ms[j]+1 if ts[j].value=='[' else j+2
                write=j<len(ts) and ts[j].value in {'=','+=','-=','++','--'}
                if i and ts[i-1].value=='delete' and t.value in roots:raise Unsupported('Navigation object/property is deleted')
                if not write:continue
                if j==i+1 and i and ts[i-1].value in {'const','let','var'}:continue
                if t.value=='content':
                    if ts is sink.tokens and j==sink.index-1 and [x.value for x in ts[i+1:j]]==['.','innerHTML']:continue
                    if j==i+1:
                        matches=[c for c in model.calls if c.tokens is ts and c.index==j+1 and c.path==('document','getElementById')]
                        if len(matches)==1:continue
                    elif [x.value for x in ts[i+1:j]] in [['.','dataset','.','routeKey']]:continue
                raise Unsupported('Navigation opts/route/page/DOM receiver is rewritten before the real sink')

    def p1_slice_rows(self,tokens,name,model,parent):
        if [t.value for t in tokens]==[name]:return True
        cs=calls(tokens)
        if len(cs)!=1 or cs[0].path!=(name,'slice') or cs[0].index!=0 or cs[0].end_index!=len(tokens)-1 or len(cs[0].args)!=2:return False
        first,last=cs[0].args
        if len(first)!=1 or first[0].kind!='id' or len(last)!=3 or [t.value for t in last[:2]]!=[first[0].value,'+']:return False
        if last[2].kind=='number':
            try:return int(last[2].value)>0
            except ValueError:return False
        if last[2].kind!='id':return False
        size=last[2].value;bindings=[]
        for ts in model.lists:
            for i,t in enumerate(ts[:-4]):
                if [x.value for x in ts[i:i+3]]==['const',size,'='] and ts[i+3].kind=='number' and ts[i+4].value==';' and model.scope(t.start)is parent:
                    try:
                        if int(ts[i+3].value)>0:bindings.append(t)
                    except ValueError:pass
        return len(bindings)==1

    def p1_known_false_key_guard(self,ts,key,selected,route=None):
        pieces=split_tokens(ts,'&&')
        for p in pieces:
            values=[t.value for t in p]
            if values==['false']:return True
            if len(p)==3 and values[0]==key and values[1] in {'===','==','!==','!='}:
                value=literal(p[2])
                if value is not None and ((selected!=value) if values[1] in {'===','=='} else (selected==value)):return True
            if route and values in [[route,'?.','redirect'],[route,'.','redirect']]:return True
            if p and p[0].value=='[':
                ms=matching(p);end=ms[0]
                if values[end+1:]==['.','includes','(',key,')']:
                    literals=[self.p1_literal(v) for v in split_tokens(p[1:end])]
                    if all(x is not None for x in literals) and selected not in literals:return True
        return False

    def p1_selected_key_stable(self,model,fn,key,selected,route=None,allowed_resolved_after=None):
        sources=[]
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts):
                if not(fn.body_start<t.start<fn.body_end) or model.enclosing_function(t.start)is not fn:continue
                if t.value not in {'if','while'} or i+1>=len(ts) or ts[i+1].value!='(':continue
                close=ms[i+1];j=close+1
                end=ms[j] if j<len(ts) and ts[j].value=='{' else next((k for k in range(j,len(ts))if ts[k].value==';'),len(ts)-1)
                if self.p1_known_false_key_guard(ts[i+2:close],key,selected,route):sources.append((ts[j].start,ts[end].end))
        for ts in model.lists:
            for i,t in enumerate(ts[:-1]):
                if t.value!=key or not(fn.body_start<t.start<fn.body_end) or model.enclosing_function(t.start)is not fn or i and ts[i-1].value in {'.','?.'}:continue
                if ts[i+1].value not in {'=','+=','-=','++','--'}:continue
                if any(a<=t.start<=b for a,b in sources):continue
                if allowed_resolved_after is not None and t.start>allowed_resolved_after and [x.value for x in ts[i+1:i+6]]==['=','resolved','.','key',';']:continue
                raise Unsupported('Selected route key is rewritten before its proven lookup/dispatch')

    def p1_child_projection(self,model,builder,array_start,selected_start,mapped):
        if len(builder.args)!=1 or len(builder.args[0])<3 or builder.args[0][0].kind!='id' or builder.args[0][1].value!='=>':raise Unsupported('Menu projection needs its exact invoked group callback')
        callbacks=[fn for fn in model.functions if fn.start==builder.args[0][1].start]
        if len(callbacks)!=1:raise Unsupported('Menu projection group callback has no unique lexical scope')
        callback=callbacks[0]
        if builder.args[0][2].value!='{':
            expression=builder.args[0][2:];cs=calls(expression)
            if not any(c.index==0 and c.end_index==len(expression)-1 and c.path==(builder.args[0][0].value,'children','forEach') for c in cs):raise Unsupported('Menu projection expression must invoke children.forEach directly')
        group=builder.args[0][0].value;child=False
        for ts in model.lists:
            ms=matching(ts)
            at=next((i for i,t in enumerate(ts)if t.start==array_start),None)
            if at is None:continue
            for element in split_tokens(ts[at+1:ms[at]]):
                try:p=self.p1_props(element)
                except Unsupported:continue
                children=p.get('children',[])
                if not children or children[0].value!='[' or matching(children).get(0)!=len(children)-1:continue
                for item in split_tokens(children[1:-1]):
                    if item and item[0].start==selected_start:
                        cp=self.p1_props(item)
                        if self.p1_literal(cp.get('type',[]))=='section':raise Unsupported('Selected menu section is not projected as a route')
                        child=True
        if not child:raise Unsupported('Selected literal route is not a direct projected menu child')
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts[:-1]):
                if t.value!='forEach' or i<2 or ts[i-1].value!='.' or ts[i+1].value!='(' or model.enclosing_function(t.start)is not callback:continue
                close=ms[i+1];args=split_tokens(ts[i+2:close])
                if len(args)!=1 or len(args[0])<3 or args[0][0].kind!='id' or [x.value for x in args[0][1:3]]!=['=>','{']:continue
                item=args[0][0].value
                if not any(args[0][0].start<pos<args[0][-1].end and item==bound for pos,bound in mapped):continue
                end=i-1
                if ts[end-1].value==')':
                    opening=ms[end-1]
                    if opening>=2 and [x.value for x in ts[opening-2:opening]]==['.','filter']:
                        predicate=ts[opening+1:end-1]
                        if len(predicate)!=7 or predicate[0].kind!='id' or [x.value for x in predicate[1:6]]!=['=>',predicate[0].value,'.','type','!=='] or literal(predicate[-1])!='section':continue
                        end=opening-2
                if end>=3 and [x.value for x in ts[end-3:end]]==[group,'.','children']:return
                if end and ts[end-1].value==')':
                    opening=ms[end-1]
                    if [x.value for x in ts[opening+1:end-1]]==[group,'.','children','||','[',']']:return
        raise Unsupported('Menu route write is not reached by its exact children.forEach projection')

    def p1_options_binding(self,dispatch,navigation):
        model=dispatch.model;found=[]
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts[:-3]):
                if t.value!='const' or [x.value for x in ts[i+1:i+3]]!=['opts','='] or not(dispatch.start<=t.start<dispatch.end) or model.enclosing_function(t.start)is not navigation:continue
                j=i+3
                if ts[j].value=='{':
                    end=ms[j];parts=split_tokens(ts[j+1:end]);views=[]
                    for p in parts:
                        if not p:continue
                        if p[0].value=='...':
                            if [x.value for x in p]!=['...','(','options','||','{','}',')']:raise Unsupported('Route options have an unknown spread')
                        else:
                            if len(p)<3 or p[0].kind!='id' or p[1].value!=':':raise Unsupported('Route opts have a computed or unknown property')
                            if p[0].value=='view':views.append(p[2:])
                    if len(views)!=1 or [x.value for x in views[0]]!=['route','.','view']:raise Unsupported('Actual opts initializer does not preserve route.view')
                    if end+1>=len(ts) or ts[end+1].value!=';':raise Unsupported('Route options result has an unknown transformation')
                    found.append(t)
                else:
                    raise Unsupported('Only the original literal const opts initializer is supported')
        if len(found)!=1:raise Unsupported('Route lacks one exact opts/default-view initializer')

    def p1_wrapper_rows_stable(self,model,wrapper,name,alias):
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts[:-1]):
                if t.value!=name or not(wrapper.body_start<t.start<wrapper.body_end) or i and ts[i-1].value in {'.','?.'}:continue
                if ts[i+1].value=='=':
                    j=i+2;prefix=[x.value for x in ts[j:j+6]]
                    if prefix!=[alias,'.','sortByUpdated','?.','(',name]:raise Unsupported('Wrapper rows are replaced by an unsupported preprocessing expression')
                    close=ms[j+4]
                    if [x.value for x in ts[close+1:close+4]]!=['||',name,';']:raise Unsupported('Wrapper row sorting must preserve its exact original fallback')
                elif ts[i+1].value in {'.','['}:
                    j=ms[i+1]+1 if ts[i+1].value=='[' else i+3
                    if j<len(ts) and ts[j].value in {'=','+=','-=','++','--'}:raise Unsupported('Wrapper rows are destructively mutated')
                if i and ts[i-1].value=='delete':raise Unsupported('Wrapper rows are deleted')
        if any(wrapper.body_start<c.start<wrapper.body_end and c.path[:1]==(name,) and c.path[1:]!=('slice',) for c in model.calls):raise Unsupported('Wrapper rows use an unsupported mutating method')

    def p1_route(self,rp,ev,callee):
        registration=self.evidence(ev['route_registration']);dispatch=self.evidence(ev['route_dispatch']);sink=self.evidence(ev['route_sink'])
        if not(registration.model is dispatch.model is sink.model):raise Unsupported('Route registration/dispatch/DOM sink must share the exact source')
        model=registration.model;route_key=rp['route_key'];route_page=rp['route_page'];route_view=rp['route_view']
        if callee[:-1] not in {('window','Pages',route_page),('Pages',route_page)}:raise Unsupported('Route page is not the exact event owner')
        selected=[];arrays=[]
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts):
                if t.value=='const' and i+3<len(ts) and ts[i+1].kind=='id' and ts[i+2].value=='=' and ts[i+3].value=='[':
                    arrays.append((ts[i+1].value,ts[i+3].start,ts[ms[i+3]].end,model.scope(t.start)))
                if t.value!='{' or not(registration.start<=t.start and ts[ms[i]].end<=registration.end):continue
                try:props=self.p1_props(ts[i:ms[i]+1])
                except Unsupported:continue
                if self.p1_literal(props.get('key',[]))==route_key:selected.append((t.start,ts[ms[i]].end,props))
        if len(selected)!=1:raise Unsupported('Route provenance needs one exact registered literal route key')
        start,end,props=selected[0]
        if self.p1_literal(props.get('page',[]))!=route_page or self.p1_literal(props.get('view',[]))!=route_view or 'redirect' in props:raise Unsupported('Route page/view differs or redirects the selected route')
        containers=[a for a in arrays if a[1]<start<end<a[2]]
        if not containers:raise Unsupported('Selected route is not in an actual literal registration array')
        collection,array_start,_,parent=min(containers,key=lambda a:a[2]-a[1])
        self.p1_collection_stable(model,collection,array_start,parent)
        # Bind the real dispatch variables, rather than trusting a route label.
        fragments=[]
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts):
                if not(dispatch.start<=t.start<dispatch.end):continue
                if [x.value for x in ts[i:i+6]]==['const','page','=','window','.','Pages'] and i+11<len(ts) and [x.value for x in ts[i+6:i+12]]==['[','route','.','page',']',';']:
                    fragments.append(t)
        if len(fragments)!=1:raise Unsupported('Route dispatch lacks its exact window.Pages[route.page] binding')
        navigation=model.enclosing_function(fragments[0].start)
        if navigation is None:raise Unsupported('Route dispatch is outside its published navigator')
        publications=[]
        for ts in model.lists:
            for i,t in enumerate(ts):
                p=path_at(ts,i,False)
                if not p or p[0]!=('window','navigateTo'):continue
                j=p[1]
                if j<len(ts) and ts[j].value in {'=','+=','-=','++','--'}:
                    if ts[j].value!='=' or j+1>=len(ts) or ts[j+1].start!=navigation.start:raise Unsupported('Published navigator is rewritten or disconnected')
                    publications.append(t)
                if i and ts[i-1].value=='delete':raise Unsupported('Published navigator is deleted')
        if len(publications)!=1:raise Unsupported('Route dispatcher is not the unique actual window.navigateTo publication')
        self.no_shadow(model,'window',None,[publications[0].start],include_parent=True)
        # Models contain separate template lists; declarations are checked on
        # the one actual containing list, not by concatenating arbitrary text.
        direct=[]
        for ts in model.lists:
            for i,t in enumerate(ts):
                if not(dispatch.start<=t.start<dispatch.end):continue
                vals=[x.value for x in ts[i:i+5]]
                if vals==['const','route','=',collection,'.']:
                    cs=[c for c in model.calls if c.tokens is ts and c.index==i+3 and c.path==(collection,'find')]
                    for c in cs:
                        if len(c.args)!=1:continue
                        a=c.args[0];v=[x.value for x in a]
                        if len(v)==7 and a[0].kind=='id' and v[1:]==['=>',a[0].value,'.','key','===','key']:direct.append(c)
        if direct:
            if len(direct)!=1 or model.enclosing_function(direct[0].start) is not navigation:raise Unsupported('Route find is outside the exact navigator')
            self.p1_selected_key_stable(model,navigation,'key',route_key)
        else:
            resolution=self.evidence(ev.get('route_resolution'))
            if resolution.model is not model:raise Unsupported('Route resolver must be in the registered navigation source')
            # Finite menu-child projection used by the original navigation:
            # literal menu -> invoked forEach -> routes[item.key]={...item,...}.
            resolver_calls=[c for c in model.calls if dispatch.contains(c) and len(c.path)==1 and model.enclosing_function(c.start) is navigation]
            linked=[]
            for c in resolver_calls:
                if len(c.args)!=1 or [t.value for t in c.args[0]]!=['key']:continue
                fn=model.declaration(c.path[0],navigation.parent)
                if fn is None or not resolution.contains(fn):continue
                f,ps,body=self.p1_function(resolution,c.path[0],navigation.parent)
                values=[t.value for t in body]
                lookups=[]
                for i in range(len(body)-8):
                    if values[i:i+3]in [['let','route','='],['const','route','=']] and body[i+3].kind=='id' and values[i+4:i+7]==['[','key',']']:lookups.append(body[i+3].value)
                if len(lookups)!=1 or not any(values[i:i+6]==['return','{','key',',','route','}'] for i in range(len(values)-5)):continue
                self.p1_selected_key_stable(model,f,'key',route_key,'route')
                for z,t in enumerate(body[:-1]):
                    if t.value=='route' and body[z+1].value=='=' and (z==0 or body[z-1].value not in {'.','?.'}):
                        if [x.value for x in body[z+2:z+6]]!=[lookups[0],'[','key',']']:raise Unsupported('Resolver replaces the actual route object')
                    if t.value=='route' and body[z+1].value in {'.','['}:
                        bm=matching(body);at=z+1
                        while at<len(body) and body[at].value in {'.','['}:at=bm[at]+1 if body[at].value=='[' else at+2
                        if at<len(body) and body[at].value in {'=','+=','-=','++','--'}:raise Unsupported('Resolver rewrites the selected route property')
                # The resolver result actually supplies the dispatch route.
                if not any([t.value for t in ts[i:i+7]]==['const','route','=','resolved','.','route',';'] for ts in model.lists for i in range(len(ts)-6) if dispatch.start<=ts[i].start<dispatch.end):continue
                if not any([t.value for t in ts[i:i+3]]==['const','resolved','='] and i+3==c.index for ts in [c.tokens] for i in range(len(ts)-3)):continue
                self.p1_selected_key_stable(model,navigation,'key',route_key,allowed_resolved_after=c.end)
                linked.append(lookups[0])
            if len(linked)!=1:raise Unsupported('Route lookup is not bound to its exact named resolver')
            route_map=linked[0]
            builders=[c for c in model.calls if c.path==(collection,'forEach') and registration.contains(c) and model.enclosing_function(c.start) is parent]
            if len(builders)!=1:raise Unsupported('Registered menu is not projected by its actual invoked route builder')
            builder=builders[0];mapped=[]
            for ts in model.lists:
                ms=matching(ts)
                for i,t in enumerate(ts):
                    if t.value!=route_map or not(builder.start<t.start<builder.end):continue
                    if i+8>=len(ts) or ts[i+1].value!='[' or ts[i+2].kind!='id' or [x.value for x in ts[i+3:i+8]]!=['.','key',']','=','{']:continue
                    obj=ts[i+7:ms[i+7]+1];parts=split_tokens(obj[1:-1]);item=ts[i+2].value
                    if not parts or [x.value for x in parts[0]]!=['...',item]:continue
                    tail=parts[1:]
                    if any(not p or p[0].value in {'...','['} or len(p)<3 or p[1].value!=':' or p[0].value in {'page','view','key'} for p in tail):continue
                    mapped.append((t.start,item))
            if not mapped:raise Unsupported('Route builder does not preserve literal child page/view/key')
            self.p1_child_projection(model,builder,array_start,start,mapped)
            # Unknown writes to the selected map slot cannot borrow a stale
            # registration. Other literal slots do not replace this object.
            for ts in model.lists:
                ms=matching(ts)
                for i,t in enumerate(ts):
                    if t.value!=route_map or i+1>=len(ts) or ts[i+1].value!='[':continue
                    close=ms[i+1];j=close+1
                    while j+1<len(ts) and ts[j].value=='.':j+=2
                    if j<len(ts) and ts[j].value in {'=','+=','-=','++','--'}:
                        if builder.start<t.start<builder.end:continue
                        key=self.p1_literal(ts[i+2:close])
                        if key is None or key==route_key:raise Unsupported('Selected route map has an unproven later write')
        # Default view, exact page render, and a real DOM node sink are bound.
        self.p1_options_binding(dispatch,navigation)
        hits=[]
        for c in model.calls:
            if c.path!=('page','render') or not sink.contains(c) or model.enclosing_function(c.start) is not navigation or len(c.args)!=1 or [t.value for t in c.args[0]]!=['opts']:continue
            pre=[t.value for t in c.tokens[max(0,c.index-4):c.index]]
            if pre==['content','.','innerHTML','='] and c.end_index+1<len(c.tokens) and c.tokens[c.end_index+1].value==';':hits.append(c)
        if len(hits)!=1:raise Unsupported('Route render has no exact DOM innerHTML sink')
        self.p1_navigation_stable(model,navigation,fragments[0].start,hits[0])
        dom=False
        for c in model.calls:
            if c.path==('document','getElementById') and c.start<hits[0].start and model.enclosing_function(c.start) is navigation and c.index>=3 and [t.value for t in c.tokens[c.index-3:c.index]] in [['let','content','='],['const','content','=']] and c.end_index+1<len(c.tokens) and c.tokens[c.end_index+1].value==';':dom=True
        if not dom:raise Unsupported('Route content is not the original DOM element binding')
        self.no_shadow(model,collection,parent,[fragments[0].start])
        return route_view

    def p1_table_contract(self,e,export,key):
        binding=self.owner_export(export,'window.PlatformUI.table','table')
        fn,params,body=self.p1_function(e,'table',binding[1])
        if len(params)<2:raise Unsupported('Real table implementation lacks columns/rows parameters')
        columns,rows=params[:2];model=e.model
        # No arbitrary destructive preprocessing.  The sole bounded rewrite
        # is a literal non-rendering prefix that spreads all original columns.
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts[:-1]):
                if not(fn.body_start<t.start<fn.body_end):continue
                if t.value==columns and ts[i+1].value=='=':
                    j=i+2
                    if j>=len(ts) or ts[j].value!='[':raise Unsupported('Table columns are replaced before rendering')
                    parts=split_tokens(ts[j+1:ms[j]])
                    if len(parts)<2 or [x.value for x in parts[-1]]!=['...',columns]:raise Unsupported('Table numbering prefix must retain all original columns')
                    for p in parts[:-1]:
                        props=self.p1_props(p)
                        if 'render' in props or self.p1_literal(props.get('key',[])) in {None,key}:raise Unsupported('Table prefix rewrites or replaces the target renderer')
                elif t.value==columns and ts[i+1].value in {'.','['}:
                    j=i+1
                    while j<len(ts) and ts[j].value in {'.','['}:j=ms[j]+1 if ts[j].value=='[' else j+2
                    if j<len(ts) and ts[j].value in {'=','+=','-=','++','--'}:raise Unsupported('Table columns/renderer mutation is unsupported')
                if t.value==rows and ts[i+1].value=='=':
                    cs=[c for c in model.calls if c.tokens is ts and c.index==i+2 and c.path==(rows,'map')]
                    if len(cs)!=1:raise Unsupported('Table rows have an unsupported replacement')
                    a=cs[0].args[0];v=[x.value for x in a]
                    if not a or a[0].value!='(':raise Unsupported('Table row prefix must be a preserving object clone')
                    am=matching(a);close=am[0];ps=split_tokens(a[1:close])
                    if len(ps)!=2 or any(len(p)!=1 or p[0].kind!='id' for p in ps) or v[close+1:close+4]!=['=>','(','{']:raise Unsupported('Unsupported table numbering row clone')
                    fields=split_tokens(a[close+4:am[close+3]])
                    if not fields or [x.value for x in fields[0]]!=['...',ps[0][0].value]:raise Unsupported('Table row clone loses the original row')
                    for p in fields[1:]:
                        if len(p)<3 or p[0].kind!='id' or p[1].value!=':' or p[0].value==key or any(x.kind not in {'id','number'} and x.value not in {'+','-','*','/','(',')'} for x in p[2:]):raise Unsupported('Table row numbering overwrites identity or uses an unproven computation')
                elif t.value==rows and ts[i+1].value in {'.','['}:
                    j=i+1
                    while j<len(ts) and ts[j].value in {'.','['}:j=ms[j]+1 if ts[j].value=='[' else j+2
                    if j<len(ts) and ts[j].value in {'=','+=','-=','++','--'}:raise Unsupported('Table rows are destructively mutated')
                if t.value=='render' and i and ts[i-1].value in {'.','?.'} and ts[i+1].value in {'=','+=','-=','++','--'}:raise Unsupported('Table renderer is rewritten')
                if t.value=='[' and i and ms.get(i) is not None and ms[i]+1<len(ts) and ts[ms[i]+1].value in {'=','+=','-=','++','--'}:raise Unsupported('Computed table-object mutation is unsupported')
        for c in model.calls:
            if not(fn.body_start<c.start<fn.body_end):continue
            if c.path[:1] in {(columns,),(rows,)} and c.path[1:] not in {('map',),('some',)}:raise Unsupported('Real table uses an unproven destructive array method')
            if any([t.value for t in a]in [[columns],[rows]] for a in c.args):raise Unsupported('Real table forwards its arrays to an unproven helper')
        candidates=[]
        for call in model.calls:
            if call.path!=(rows,'map') or model.enclosing_function(call.start) is not fn or len(call.args)!=1:continue
            a=call.args[0]
            if len(a)!=3 or a[0].kind!='id' or a[1].value!='=>' or a[2].kind!='template':continue
            row=a[0].value;tr=a[2]
            for kind,part in tr.parts:
                if kind!='expression' or not self.p1_active_part(tr,part):continue
                for cc in calls(part):
                    if cc.path!=(columns,'map') or cc.index!=0 or [t.value for t in part[cc.end_index+1:]]!=['.','join','(',"''",')'] or len(cc.args)!=1:continue
                    ca=cc.args[0]
                    if len(ca)!=3 or ca[0].kind!='id' or ca[1].value!='=>' or ca[2].kind!='template':continue
                    col=ca[0].value;td=ca[2];html,_=template_markup(td)
                    if '<td' not in html or '</td>' not in html:continue
                    for pk,pv in td.parts:
                        if pk!='expression' or not self.p1_active_part(td,pv):continue
                        for render in calls(pv):
                            if render.path!=(col,'render') or len(render.args)!=2 or [t.value for t in render.args[0]]!=[row,'[',col,'.','key',']'] or [t.value for t in render.args[1]]!=[row]:continue
                            prefix=[t.value for t in pv[:render.index]];suffix=pv[render.end_index+1:]
                            if not(prefix==[col,'.','render','?'] and suffix and suffix[0].value==':' or not prefix and not suffix):continue
                            candidates.append(call)
        candidates=list({c.start:c for c in candidates}.values())
        if len(candidates)!=1:raise Unsupported('Real table lacks exact rows.map/columns.map/col.render(row[col.key],row) cell flow')
        call=candidates[0];ts=call.tokens
        # The renderer result must survive its map/join into a named body
        # variable that is directly inserted into a returned tbody template.
        before=call.index
        while before and ts[before-1].value not in {';','{','}'}:before-=1
        statement=ts[before:call.index]
        if len(statement)<3 or statement[0].value not in {'const','let'} or statement[1].kind!='id' or statement[2].value!='=':raise Unsupported('Table row output is not bound to its returned body')
        name=statement[1].value;prefix=[t.value for t in statement[3:]]
        if prefix not in [[],[rows,'.','length','?']]:raise Unsupported('Unsupported table row-body conditional')
        returned=self.p1_returns(model,fn,body)
        self.p1_single_top_return(model,fn,returned)
        if not any('<tbody>' in template_markup(t)[0] and '</tbody>' in template_markup(t)[0] and any(k=='expression' and [x.value for x in p]==[name] and self.p1_active_part(t,p) for k,p in t.parts) for t in returned):raise Unsupported('Table row body is not inserted into its active returned tbody')

    def table_return_provenance(self,entry,e,callee,owner_binding,alias):
        rp=entry.get('render_provenance')
        if not isinstance(rp,dict) or rp.get('version')!='bounded-table-return@1' or rp.get('reachability')!='conditional_only' or rp.get('route_options_policy')!='default_route_view_no_override':raise Unsupported('Unsupported bounded table-return provenance/conditional route policy')
        if entry.get('handler_helper_evidence') or not alias:raise Unsupported('P1 requires a direct event and the original PlatformUI alias')
        for name in ('route_key','route_page','route_view','exported_render_symbol','render_delegate_symbol','view_parameter','view_variable','column_key_literal','wrapper_symbol','wrapper_columns_parameter','wrapper_rows_parameter','exact_event_target_symbol'):
            if not isinstance(rp.get(name),str) or not rp[name]:raise Unsupported('Missing bounded table provenance field: '+name)
        for name in ('wrapper_columns_argument_index','wrapper_rows_argument_index','table_columns_argument_index','table_rows_argument_index','event_object_argument_index'):
            if type(rp.get(name))is not int or rp[name]<0:raise Unsupported('Invalid bounded parameter slot: '+name)
        if rp['table_sink_callee']!='PlatformUI.table' or (rp['table_columns_argument_index'],rp['table_rows_argument_index'])!=(0,1):raise Unsupported('P1 table contract uses exact PlatformUI.table columns/rows slots')
        target,parent,props,_=owner_binding
        if target.symbol!=rp['exact_event_target_symbol'] or props.get(rp['exported_render_symbol'])!=rp['exported_render_symbol']:raise Unsupported('Table provenance target/render is not the exact owner export')
        ev=rp.get('evidence',{})
        route_view=self.p1_route(rp,ev,callee)
        re=self.evidence(ev['render']);de=self.evidence(ev['delegate']);we=self.evidence(ev['wrapper']);te=self.evidence(ev['table_implementation']);tx=self.evidence(ev['table_export'])
        if not(re.model is de.model is we.model is e.model) or te.model is not tx.model:raise Unsupported('Page render/delegate/wrapper or real table sources are disconnected')
        model=e.model;render,params,body=self.p1_function(re,rp['exported_render_symbol'],parent);delegate,dp,db=self.p1_function(de,rp['render_delegate_symbol'],parent);wrapper,wp,wb=self.p1_function(we,rp['wrapper_symbol'],parent)
        if params!=[rp['view_parameter']] or dp:raise Unsupported('Render/delegate parameters differ from bounded view provenance')
        view=rp['view_variable'];options=rp['view_parameter'];assign=[view,'=',options,'?.','view','||',view]
        if not any([t.value for t in body[i:i+7]]in [assign,[view,'=',options,'.','view','||',view]] for i in range(len(body)-6)):raise Unsupported('Exported render does not take its view from the exact options parameter')
        view_writes=[t for ts in model.lists for i,t in enumerate(ts[:-1]) if t.value==view and render.body_start<t.start<render.body_end and ts[i+1].value in {'=','+=','-=','++','--'} and (i==0 or ts[i-1].value not in {'.','?.'})]
        if len(view_writes)!=1:raise Unsupported('Exported render rewrites its selected view')
        for ts in model.lists:
            bm=matching(ts)
            for i,t in enumerate(ts[:-1]):
                if t.value!=options or not(render.body_start<t.start<render.body_end) or i and ts[i-1].value in {'.','?.'}:continue
                at=i+1
                while at<len(ts) and ts[at].value in {'.','['}:at=bm[at]+1 if ts[at].value=='[' else at+2
                if at<len(ts) and ts[at].value in {'=','+=','-=','++','--'}:raise Unsupported('Exported render mutates its original route options')
        returns=[c for c in model.calls if c.path==(delegate.symbol,) and model.enclosing_function(c.start)is render and c.index and c.tokens[c.index-1].value=='return' and not any(c.args)]
        if len(returns)!=1:raise Unsupported('Exported render does not return the unique named delegate')
        dc=returns[0];ms=matching(body)
        # Resolve only simple view comparisons; all original non-view guards
        # remain conditional. A matching view branch returning elsewhere
        # cannot borrow the later fallback delegate.
        guarded=[]
        for i,t in enumerate(body):
            if t.value!='if' or i+1>=len(body) or body[i+1].value!='(':continue
            close=ms[i+1];condition=body[i+2:close]
            j=close+1;end=ms[j] if j<len(body) and body[j].value=='{' else next((k for k in range(j,len(body))if body[k].value==';'),len(body)-1)
            guarded.append((body[j].start,body[end].end))
            truth=None
            if len(condition)==1 and condition[0].value in {'true','false'}:truth=condition[0].value=='true'
            elif len(condition)==3 and condition[0].value==view and condition[1].value in {'===','==','!==','!='}:
                value=literal(condition[2]);truth=(route_view==value)
                if condition[1].value in {'!==','!='}:truth=not truth
            if truth is None:continue
            if not truth and body[j].start<=dc.start<=body[end].end:raise Unsupported('Selected route/view excludes the declared delegate branch')
            if truth and any(x.value=='return' for x in body[j:end+1]) and not(body[j].start<=dc.start<=body[end].end):raise Unsupported('Selected route/view returns a different branch before the declared delegate')
        if any(t.value=='return' and t.start<dc.start and t is not dc.tokens[dc.index-1] and model.enclosing_function(t.start)is render and not any(a<=t.start<=b for a,b in guarded) for t in body):raise Unsupported('An unconditional return makes the declared render delegate unreachable')
        returned=self.p1_returns(model,delegate,db);wc=self.p1_returned_call(model,delegate,returned,(wrapper.symbol,))
        ci=rp['wrapper_columns_argument_index'];ri=rp['wrapper_rows_argument_index']
        if ci>=len(wp) or ri>=len(wp) or ci==ri or wp[ci]!=rp['wrapper_columns_parameter'] or wp[ri]!=rp['wrapper_rows_parameter'] or ci>=len(wc.args) or ri>=len(wc.args):raise Unsupported('Wrapper argument/parameter slots do not bind the actual columns and rows')
        incoming=wc.args[ri]
        if [t.value for t in incoming]==['[',']']:raise Unsupported('Delegate supplies an explicitly empty row set')
        if len(incoming)==1 and incoming[0].kind=='id':
            rowname=incoming[0].value
            if any([t.value for t in ts[i:i+6]]in [[kind,rowname,'=','[',']',';']for kind in ['const','let','var']] for ts in model.lists for i in range(len(ts)-5) if delegate.body_start<ts[i].start<wc.start):raise Unsupported('Delegate row binding is explicitly empty')
        cols=wc.args[ci]
        if not cols or cols[0].value!='[' or matching(cols).get(0)!=len(cols)-1:raise Unsupported('Actual wrapper columns are not one literal array')
        objects=[self.p1_props(p) for p in split_tokens(cols[1:-1]) if p]
        matches=[p for p in objects if self.p1_literal(p.get('key',[]))==rp['column_key_literal']]
        if len(matches)!=1:raise Unsupported('Actual literal column key differs or is duplicated')
        value=matches[0].get('render',[]);vm=matching(value)
        if not value or value[0].value!='(':raise Unsupported('Selected column needs an exact literal render arrow')
        close=vm[0];ps=split_tokens(value[1:close]);v=[t.value for t in value]
        if any(len(p)!=1 or p[0].kind!='id' for p in ps) or v[close+1:close+2]!=['=>'] or len(value)!=close+3 or value[-1].kind!='template':raise Unsupported('Selected renderer is not one returned arrow template')
        template=value[-1]
        if not e.contains(template):raise Unsupported('Actual entry quote does not cover the selected column template')
        if 'nested_event_path' in rp:template=self.p3_nested_event_template(rp['nested_event_path'],e,template,[p[0].value for p in ps])
        helper=None
        if 'event_helper_provenance' in rp:
            if 'nested_event_path' in rp:raise Unsupported('Delegated escaped helper does not yet compose with a nested conditional event path')
            self.p4_renderer_readonly(model,template,[p[0].value for p in ps])
            helper=self.p4_delegated_event_helper(rp['event_helper_provenance'],entry,e,callee,parent,template.start,alias,te,tx)
        html,expressions=template_markup(template);parser=TableHTMLContext(expressions);parser.feed(html);parser.close()
        valid=[]
        for evname,h in parser.handlers:
            if evname!=entry['event'] or not exact_handler(h,callee,expressions,helper):continue
            if helper:
                origin=self.p4_helper_identity_argument(h,expressions,[p[0].value for p in ps],rp['column_key_literal'],rp['event_object_argument_index'])
                if origin=='row':self.p2_protected_escapes(model,{ps[1][0].value},value[-1].start,value[-1].end,label='P4 selected row identity')
                if origin:valid.append(h)
            elif self.p1_identity_argument(h,callee,expressions,[p[0].value for p in ps],rp['column_key_literal'],rp['event_object_argument_index']):valid.append(h)
        if not valid:raise Unsupported('Actual column event/callee/row identity does not bind the selected object key')
        wt=self.p1_returns(model,wrapper,wb);tc=self.p1_returned_call(model,wrapper,wt,(alias,'table'))
        if len(tc.args)<2 or [t.value for t in tc.args[0]]!=[wp[ci]]:raise Unsupported('Wrapper does not pass its exact original columns into the real table')
        rowarg=tc.args[1]
        if not self.p1_slice_rows(rowarg,wp[ri],model,parent):raise Unsupported('Wrapper table receives a different or deterministically empty rows expression')
        self.p1_wrapper_rows_stable(model,wrapper,wp[ri],alias)
        # A columns parameter may not be overwritten, filtered or passed to
        # an arbitrary mutating helper before the one proven table call.
        uses=[t for ts in model.lists for t in ts if t.value==wp[ci] and wrapper.body_start<t.start<wrapper.body_end and model.enclosing_function(t.start) is wrapper]
        if len(uses)!=1 or uses[0] is not tc.args[0][0]:raise Unsupported('Wrapper columns have an unsupported rewrite or forwarding use')
        self.alias(self.evidence(entry['ui_alias_evidence']),alias,parent)
        self.no_shadow(model,alias,parent,[template.start,tc.start]);self.no_shadow(model,wrapper.symbol,parent,[wc.start]);self.no_shadow(model,delegate.symbol,parent,[dc.start])
        self.p1_table_contract(te,tx,rp['column_key_literal'])
        return ('bounded-table-return',template)

    def p4_returned_raw_table(self,entry,e,callee,binding,alias):
        rp=entry.get('raw_table_control_provenance')
        if not isinstance(rp,dict)or rp.get('version')!='bounded-returned-raw-table-control@1'or rp.get('reachability')!='conditional_only'or rp.get('route_options_policy')!='default_route_view_no_override':raise Unsupported('Unsupported P4 returned raw-table profile')
        if not alias or 'handler_helper_evidence'in entry or any(k in entry for k in ['direct_table_provenance','opening_provenance','render_provenance','quoted_control_provenance']):raise Unsupported('P4 raw table cannot borrow another entry mode or helper')
        for name in ['route_key','route_page','route_view','exported_render_symbol','render_delegate_symbol','view_parameter','column_key_literal']:
            if not isinstance(rp.get(name),str)or not rp[name]:raise Unsupported('Missing P4 raw table field: '+name)
        column=rp.get('column_index');index=rp.get('event_object_argument_index')
        if type(column)is not int or column<0 or type(index)is not int or index<0:raise Unsupported('P4 raw table needs exact literal column/argument slots')
        _,parent,props,_=binding
        if props.get(rp['exported_render_symbol'])!=rp['exported_render_symbol']:raise Unsupported('P4 raw table render is not published by the exact owner')
        ev=rp.get('evidence',{});self.p1_route(rp,ev,callee);re=self.evidence(ev.get('render'));de=self.evidence(ev.get('delegate'));te=self.evidence(ev.get('table_implementation'));tx=self.evidence(ev.get('table_export'))
        if re.model is not e.model or de.model is not e.model or te.model is not tx.model:raise Unsupported('P4 raw table render/delegate/library evidence disconnected')
        delegate,body=self.p4_quoted_render_delegate(re,de,parent,rp);template=self.p4_direct_template_return(e.model,delegate,body)
        cs=[c for c in e.model.calls if c.path==(alias,'table')and e.model.enclosing_function(c.start)is delegate and any(k=='expression'and c.tokens is part and c.index==0 and c.end_index==len(part)-1 and self.p1_active_part(template,part)for k,part in template.parts)]
        if len(cs)!=1 or len(cs[0].args)<2:raise Unsupported('P4 raw table needs one exact active returned table')
        tc=cs[0];cols=tc.args[0]
        if not cols or cols[0].value!='['or matching(cols).get(0)!=len(cols)-1:raise Unsupported('P4 raw table requires actual literal columns')
        columns=split_tokens(cols[1:-1]);
        if column>=len(columns):raise Unsupported('P4 raw table selected column is absent')
        cp=self.p1_props(columns[column])
        if self.p1_literal(cp.get('key',[]))!=rp['column_key_literal']:raise Unsupported('P4 raw table selected index/key differs')
        renderer=cp.get('render',[])
        if not renderer:raise Unsupported('P4 raw table selected column lacks renderer')
        if renderer[0].kind=='id':ps=[renderer[0].value];at=1
        elif renderer[0].value=='(':
            end=matching(renderer).get(0);params=split_tokens(renderer[1:end]);
            if any(len(p)!=1 or p[0].kind!='id'for p in params):raise Unsupported('P4 raw renderer needs exact plain parameters')
            ps=[p[0].value for p in params];at=end+1
        else:raise Unsupported('P4 raw table renderer form unsupported')
        if not ps or len(set(ps))!=len(ps)or len(renderer)!=at+2 or renderer[at].value!='=>'or renderer[-1].kind!='template':raise Unsupported('P4 raw renderer is not an exact returned arrow template')
        rt=renderer[-1]
        if not e.contains(rt):raise Unsupported('P4 raw entry quote misses actual renderer')
        self.p4_renderer_readonly(e.model,rt,ps)
        html,expressions=template_markup(rt);parser=TableHTMLContext(expressions);parser.feed(html);parser.close()
        valid=[]
        for event,handler in parser.handlers:
            if event!=entry['event'] or not exact_handler(handler,callee,expressions,None):continue
            origin=self.p4_raw_identity_argument(handler,expressions,ps,rp['column_key_literal'],index)
            if origin=='row':self.p2_protected_escapes(e.model,{ps[1]},rt.start,rt.end,label='P4 selected row identity')
            if origin:valid.append(handler)
        if not valid:raise Unsupported('P4 raw table event does not bind exact row identity/callee')
        self.p2_table_rows(e.model,delegate,tc,alias);self.p1_table_contract(te,tx,rp['column_key_literal'])
        ae=self.evidence(entry['ui_alias_evidence']);self.p2_const_alias(ae,alias,'PlatformUI',parent,tc.start);self.no_shadow(e.model,callee[0],None,[rt.start],include_parent=True)
        return e,callee

    def p4_quoted_html(self,token):
        # Decode only common quoted-string escapes needed by original HTML.
        # Unknown JS escapes/line continuations stay unsupported, never eval.
        if token.kind!='string' or len(token.value)<2:raise Unsupported('P4 control is not one quoted literal')
        src=token.value[1:-1];out=[];i=0;escapes={'\\':'\\',"'":"'",'"':'"','n':'\n','r':'\r','t':'\t'}
        while i<len(src):
            if src[i]!='\\':out.append(src[i]);i+=1;continue
            i+=1
            if i==len(src)or src[i]not in escapes:raise Unsupported('P4 quoted HTML has an unsupported escape')
            out.append(escapes[src[i]]);i+=1
        return ''.join(out)

    def p4_direct_template_return(self,model,fn,body):
        templates=self.p1_returns(model,fn,body)
        if len(templates)!=1:raise Unsupported('P4 delegate must have one exact returned template')
        template=templates[0]
        # The selected template itself must be a direct function-body return;
        # guarded error returns remain conditional, not generalized execution.
        for ts in model.lists:
            ms=matching(ts);start=next((i for i,t in enumerate(ts)if t.start==fn.body_start),None)
            if start is None:continue
            i=start+1;statement=[]
            while i<ms[start]:
                t=ts[i]
                if t.value=='return'and i+1<len(ts)and ts[i+1]is template:
                    if statement:raise Unsupported('P4 returned control template is in a guarded/dead statement')
                    return template
                if t.value=='return'and not statement:raise Unsupported('P4 unconditional return hides the selected control template')
                if t.value in {'while','for','do','switch','try','throw','with'}:raise Unsupported('P4 delegate has an unsupported flow statement before its control return')
                if t.value=='if'and i+1<len(ts)and ts[i+1].value=='(':
                    close=ms[i+1];cond=[x.value for x in ts[i+2:close]]
                    if cond==['true']:raise Unsupported('P4 known unconditional if return/branch precedes selected control')
                if t.value==';':statement=[];i+=1;continue
                statement.append(t)
                if t.value in {'(','[','{'}:
                    i=ms[i]+1
                    # A complete statement block is a statement boundary.
                    if t.value=='{':statement=[]
                else:i+=1
        raise Unsupported('P4 missing direct returned control template')

    def p4_quoted_render_delegate(self,re,de,parent,rp):
        render,params,body=self.p1_function(re,rp['exported_render_symbol'],parent)
        delegate,dp,db=self.p1_function(de,rp['render_delegate_symbol'],parent)
        if params!=[rp['view_parameter']]or dp:raise Unsupported('P4 quoted render/delegate parameter mismatch')
        view=rp['route_view'];option=params[0];ms=matching(body);found=[];i=0
        # Finite original dispatch: a series of option.view literal checks,
        # each returning one named call, then a single fallback return.
        while i<len(body):
            if body[i].value=='if'and i+1<len(body)and body[i+1].value=='(':
                end=ms[i+1];cond=body[i+2:end];v=[t.value for t in cond]
                if len(v)!=5 or v[0]!=option or v[1]not in {'.','?.'}or v[2:4]!=['view','===']or literal(cond[-1])is None:raise Unsupported('P4 quoted dispatch requires exact option.view literal branches')
                j=end+1
                if j>=len(body)or body[j].value!='return':raise Unsupported('P4 quoted dispatch branch is not an exact returned call')
                stop=next((z for z in range(j+1,len(body))if body[z].value==';'),None)
                if stop is None:raise Unsupported('P4 quoted dispatch branch lacks statement boundary')
                expr=body[j+1:stop];cs=calls(expr)
                if len(cs)!=1 or cs[0].index!=0 or cs[0].end_index!=len(expr)-1:raise Unsupported('P4 quoted dispatch has an unsupported result expression')
                c=cs[0]
                if literal(cond[-1])==view:
                    if c.path!=(delegate.symbol,)or any(c.args):raise Unsupported('P4 selected route/view returns another delegate')
                    found.append(c)
                i=stop+1;continue
            if body[i].value=='return':
                if not found:raise Unsupported('P4 route/view has no reachable declared delegate')
                break
            raise Unsupported('P4 quoted dispatch has an unproven rewrite or statement')
        if len(found)!=1:raise Unsupported('P4 quoted dispatch needs one exact selected view branch')
        self.no_shadow(re.model,delegate.symbol,parent,[found[0].start])
        return delegate,db

    def p4_quoted_return_control(self,entry,e,callee,binding,alias):
        rp=entry.get('quoted_control_provenance')
        if not isinstance(rp,dict)or rp.get('version')!='bounded-returned-quoted-control@1'or rp.get('reachability')!='conditional_only'or rp.get('route_options_policy')!='default_route_view_no_override':raise Unsupported('Unsupported P4 returned quoted control profile')
        if not alias or 'handler_helper_evidence'in entry or any(k in entry for k in ['direct_table_provenance','opening_provenance','render_provenance','raw_table_control_provenance']):raise Unsupported('P4 quoted control cannot borrow another entry mode or helper')
        for name in ['route_key','route_page','route_view','exported_render_symbol','render_delegate_symbol','view_parameter','ui_helper']:
            if not isinstance(rp.get(name),str)or not rp[name]:raise Unsupported('Missing P4 quoted field: '+name)
        helper=rp['ui_helper'];slot=rp.get('html_argument_index')
        if helper not in {'pageHeader','toolbar'}or type(slot)is not int or slot not in ({2}if helper=='pageHeader'else{0,1}):raise Unsupported('P4 quoted control uses an unsupported UI helper/HTML slot')
        target,parent,props,_=binding
        if props.get(rp['exported_render_symbol'])!=rp['exported_render_symbol']:raise Unsupported('P4 quoted control render is not published by its actual owner')
        ev=rp.get('evidence',{});self.p1_route(rp,ev,callee)
        re=self.evidence(ev.get('render'));de=self.evidence(ev.get('delegate'));ui_e=self.evidence(ev.get('ui_implementation'));ui_x=self.evidence(ev.get('ui_export'))
        if re.model is not e.model or de.model is not e.model or ui_e.model is not ui_x.model:raise Unsupported('P4 quoted control render/delegate/UI evidence is disconnected')
        delegate,body=self.p4_quoted_render_delegate(re,de,parent,rp);template=self.p4_direct_template_return(e.model,delegate,body)
        # The literal is an exact original argument to a real helper call whose
        # whole return is in an active interpolation of the returned template.
        hits=[]
        for call in e.model.calls:
            if call.path!=(alias,helper)or e.model.enclosing_function(call.start)is not delegate or slot>=len(call.args):continue
            literal_arg=call.args[slot]
            if len(literal_arg)!=1 or literal_arg[0].kind!='string'or not e.contains(literal_arg[0]):continue
            if not any(kind=='expression'and call.tokens is part and call.index==0 and call.end_index==len(part)-1 and self.p1_active_part(template,part)for kind,part in template.parts):continue
            html=self.p4_quoted_html(literal_arg[0]);parser=TableHTMLContext({});parser.feed(html);parser.close()
            if any(event==entry['event']and exact_handler(handler,callee,{},None)for event,handler in parser.handlers):hits.append(call)
        if len(hits)!=1:raise Unsupported('P4 quoted control lacks a unique active exact helper argument event')
        ub=self.owner_export(ui_x,'window.PlatformUI.'+helper,helper);uf,ps,body=self.p1_function(ui_e,helper,ub[1])
        if slot>=len(ps):raise Unsupported('P4 real UI helper lacks selected HTML argument')
        returns=self.p1_returns(ui_e.model,uf,body);self.p1_single_top_return(ui_e.model,uf,returns)
        param=ps[slot];active=[]
        for token in returns:
            for kind,part in token.parts:
                if kind=='expression'and [x.value for x in part]in [[param],[param,'||',"''"],[param,'||','""']]and self.p1_active_part(token,part):active.append(part)
        if len(active)!=1:raise Unsupported('P4 UI helper does not return its exact unescaped HTML parameter in active content')
        uses=[t for ts in ui_e.model.lists for t in ts if t.value==param and uf.body_start<t.start<uf.body_end and ui_e.model.enclosing_function(t.start)is uf]
        if len(uses)!=1:raise Unsupported('P4 UI HTML parameter is rewritten or forwarded elsewhere')
        ae=self.evidence(entry['ui_alias_evidence']);self.alias(ae,alias,parent)
        self.p2_const_alias(ae,alias,'PlatformUI',parent,hits[0].start)
        self.no_shadow(e.model,alias,parent,[hits[0].start]);self.real_ui_owner(ui_e.model,('window','PlatformUI'),ub[3])
        return e,callee

    def p4_delegated_event_helper(self,spec,entry,e,callee,parent,use,alias,table,export):
        # Explicit composition of the existing P1 returned-table provenance
        # and P2's fixed HTML-escape/JSON-arguments helper.  No recursive
        # handler search or implicit helper support is added to old proofs.
        if not isinstance(spec,dict) or spec.get('version')!='bounded-fixed-escaped-table-event@1' or spec.get('reachability')!='conditional_only':raise Unsupported('Unsupported explicit delegated escaped-event helper provenance')
        self.p2_const_alias(self.evidence(entry['ui_alias_evidence']),alias,'PlatformUI',parent,use)
        helper=self.p2_escaped_helper(spec,parent,use,escape_use_validator=lambda name:self.p4_escape_callback_uses(spec,e,name,parent))
        if helper['model']is not e.model or helper['owner']+(helper['method'],)!=callee:raise Unsupported('Delegated helper literal owner/method differs from the exact selected event')
        escape=self.evidence(spec.get('escape_implementation_evidence'))
        if not(escape.model is table.model is export.model):raise Unsupported('Delegated helper escape/table library provenance is disconnected')
        self.p2_escape(escape,export)
        return helper

    def p4_renderer_readonly(self,model,root,params):
        # Preserve the exact selected identity, not general renderer purity.
        # Direct parameter/field writes are rejected throughout the template;
        # whole-row escape is checked separately only for a selected row[key]
        # identity. A distinct lexical value parameter cannot be rebound by
        # forwarding the row to an original sibling predicate.
        self.p2_state_mutators(model,set(params),root.start,root.end,label='P4 renderer identity')
        self.p4_no_write_contexts(model,set(params),root.start,root.end)

    def p4_no_write_contexts(self,model,names,first,last,allowed=()):
        lower,upper=first,last
        comparisons={'===','!==','==','!=','<=','>='}
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts):
                if t.kind!='id' or t.value not in names or not(lower<t.start<upper) or i and ts[i-1].value in {'.','?.'} or any(t is token for token in allowed):continue
                parsed=path_at(ts,i,False,partial=True)
                if not parsed:continue
                _,end=parsed
                while end<len(ts) and ts[end].value in {'.','['}:end=ms[end]+1 if ts[end].value=='[' else end+2
                first,last=i,end
                while first>0 and ts[first-1].value=='(' and ms.get(first-1)==last:first-=1;last+=1
                if first!=i and last<len(ts) and ts[last].value in {'.','?.','['}:raise Unsupported('P4 readonly contract has an unsupported parenthesized receiver property use')
                if first and ts[first-1].value in {'++','--','delete'}:raise Unsupported('P4 renderer parameter/identity has a prefix write')
                if last<len(ts) and ts[last].value in {'++','--','in','of'}:raise Unsupported('P4 renderer parameter/identity has a postfix or iteration write')
                at=last;operators=''
                while at<len(ts) and ts[at].kind=='punct' and ts[at].value not in {'(',')','[',']','{','}',',',';',':','?'}:
                    operators+=ts[at].value
                    at+=1
                if '=' in operators and operators not in comparisons:raise Unsupported('P4 renderer parameter/identity has an assignment or compound write')
                # Explicit destructuring targets are unsupported writes, not
                # a reason to interpret general JavaScript assignment syntax.
                for opening,closing in ms.items():
                    if opening<i<closing and ts[opening].value in {'[','{'} and closing+1<len(ts) and ts[closing+1].value=='=':raise Unsupported('P4 renderer parameter/identity appears in a destructuring write')

    def p4_escape_callback_uses(self,spec,e,callback,parent):
        # Only explicit, source-bound uses of the validated pure escape
        # function as Array.map's callback. Receiver objects keep P2's
        # default escape rejection; this is not an alias graph or a blanket
        # exception for every use in another function.
        records=spec.get('escape_callback_uses',[])
        if not isinstance(records,list) or len(records)>8:raise Unsupported('P4 escape callback evidence must be a finite list')
        allowed=[]
        for record in records:
            if not isinstance(record,dict) or record.get('version')!='bounded-local-string-array-map@1' or not IDENT.fullmatch(record.get('array_symbol','')):raise Unsupported('Unsupported explicit readonly escape callback provenance')
            ce=self.evidence(record.get('evidence'));model=ce.model;name=record['array_symbol']
            if model is not e.model:raise Unsupported('Escape callback use is from another original page')
            maps=[c for c in model.calls if ce.contains(c) and c.path==(name,'map') and len(c.args)==1 and len(c.args[0])==1 and c.args[0][0].kind=='id' and c.args[0][0].value==callback]
            if len(maps)!=1:raise Unsupported('Escape callback evidence lacks one exact local array map call')
            call=maps[0];fn=model.enclosing_function(call.start)
            if fn is None or not ce.contains(fn):raise Unsupported('Readonly escape callback needs its complete original local function scope')
            bindings=[(position,scope) for bound,position,scope in model.variable_bindings() if bound==name and scope is fn]
            if len(bindings)!=1:raise Unsupported('Readonly escape receiver lacks one original local const binding')
            declarations=[]
            for ts in model.lists:
                for i,t in enumerate(ts[:-4]):
                    if t.value!=name or [x.value for x in ts[i+1:i+4]]!=['=','[',']'] or ts[i+4].value not in {',',';'} or model.enclosing_function(t.start)is not fn:continue
                    binding=next((j for j,x in enumerate(ts) if x.start==bindings[0][0]),None)
                    if binding is not None and binding<i and ts[binding].value=='const' and not any(x.value==';' for x in ts[binding:i]):declarations.append(t)
            if len(declarations)!=1 or declarations[0].start>=call.start:raise Unsupported('Readonly escape receiver is not the original const empty array')
            self.no_shadow(model,callback,parent,[call.args[0][0].start])
            # The admitted original page has no prototype references.  Reject
            # them instead of inventing a prototype-alias mutation graph or
            # pretending to verify the whole browser's builtins.
            for ts in model.lists:
                for i,t in enumerate(ts):
                    if t.kind!='id' or i and ts[i-1].value in {'.','?.'}:continue
                    parsed=path_at(ts,i,False,partial=True)
                    if parsed and (parsed[0][:2]==('Array','prototype') or parsed[0][:3]==('window','Array','prototype')):raise Unsupported('Readonly escape callback source references Array.prototype outside its finite contract')
            valid_receivers={declarations[0].start,call.start}
            for push in model.calls:
                if not(fn.body_start<push.start<fn.body_end) or push.path!=(name,'push'):continue
                if model.enclosing_function(push.start)is not fn or push.start>=call.start or len(push.args)!=1 or len(push.args[0])!=1 or push.args[0][0].kind!='string' or literal(push.args[0][0])is None:raise Unsupported('Readonly escape array has an unsupported push or deferred write')
                valid_receivers.add(push.start)
            for ts in model.lists:
                for i,t in enumerate(ts):
                    if t.kind!='id' or t.value!=name or not(fn.body_start<t.start<fn.body_end) or i and ts[i-1].value in {'.','?.'}:continue
                    if model.enclosing_function(t.start)is not fn:raise Unsupported('Readonly escape array is captured by another function')
                    if t.start in valid_receivers:continue
                    # Exact original consumption only: the receiver begins
                    # its interpolation's direct `array.length ?` condition.
                    # Unknown, parenthesized, prefix/compound/destructuring
                    # and iteration forms all remain pending.
                    if i==0 and [x.value for x in ts[i+1:i+4]]==['.','length','?']:continue
                    raise Unsupported('Readonly escape array is rebound, copied, forwarded or has an unknown member use')
            token=call.args[0][0]
            if any(token is previous for previous in allowed):raise Unsupported('Readonly escape callback evidence is duplicated')
            allowed.append(token)
        return allowed

    def p4_helper_identity_argument(self,handler,expressions,params,key,index):
        # The first argument is the fixed literal operation name; the exact
        # original object remains at the declared event-argument slot.
        tokens=expressions.get(handler.strip(),[]);cs=calls(tokens)
        if len(cs)!=1 or len(cs[0].args)<=index+1:return None
        return self.p4_identity_origin(cs[0].args[index+1],params,key)

    def p4_raw_identity_argument(self,handler,expressions,params,key,index):
        try:cs=calls(lex(handler.strip().removesuffix(';').strip()))
        except Unsupported:return None
        if len(cs)!=1 or len(cs[0].args)<=index or len(cs[0].args[index])!=1:return None
        arg=cs[0].args[index][0];marker=literal(arg) if arg.kind=='string' else arg.value
        return self.p4_identity_origin(expressions.get(marker,[]),params,key)

    def p4_identity_origin(self,expression,params,key):
        values=[t.value for t in expression]
        if params and values==[params[0]]:return 'value'
        if len(params)>1 and (values==[params[1],'.',key] or len(expression)==4 and values[:2]==[params[1],'['] and literal(expression[2])==key and values[-1]==']'):return 'row'
        return None

    def p4_flow_path(self,tokens,index=0,keymap=None,aliases=None):
        keymap=keymap or {};aliases=aliases or {}
        if index>=len(tokens)or tokens[index].kind!='id':return None
        root=tokens[index].value;parts=list(aliases.get(root,(root,)));i=index+1
        while i<len(tokens):
            if i+1<len(tokens)and tokens[i].value=='.'and tokens[i+1].kind=='id':parts.append(tokens[i+1].value);i+=2
            elif i+2<len(tokens)and tokens[i].value=='['and tokens[i+2].value==']':
                key=literal(tokens[i+1])if tokens[i+1].kind=='string'else keymap.get(tokens[i+1].value)
                if not isinstance(key,str)or not STATIC_KEY.fullmatch(key):break
                parts.append(key);i+=3
            else:break
        return tuple(parts),i

    def p4_flow_const_literal(self,spec,model,parent):
        e=self.evidence(spec.get('evidence'));name=spec.get('symbol');value=spec.get('literal')
        if e.model is not model or not IDENT.fullmatch(name or '')or not isinstance(value,str):raise Unsupported('Flow constant key is disconnected or unnamed')
        found=[]
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts):
                if t.value!='const'or model.scope(t.start)is not parent:continue
                j=i+1
                while j+2<len(ts)and ts[j].kind=='id'and ts[j+1].value=='=':
                    z=j+2
                    while z<len(ts)and ts[z].value not in {',',';'}:z=ms[z]+1 if ts[z].value in {'(','[','{'}else z+1
                    if ts[j].value==name and z==j+3 and literal(ts[j+2])==value and e.start<=t.start and ts[j+2].end<=e.end:found.append(ts[j])
                    if z>=len(ts)or ts[z].value!=',':break
                    j=z+1
        if len(found)!=1:raise Unsupported('Flow key lacks one exact same-scope const literal')
        self.p2_no_owner_writes(model,[(name,)],[found[0]])
        return name,value

    def p4_flow_root_alias(self,spec,model,parent):
        name=spec.get('alias');kind=spec.get('kind');target=spec.get('target','window')
        if not IDENT.fullmatch(name or '')or target!='window':raise Unsupported('Flow publication alias must bind the original window root')
        evidence=self.evidence(spec.get('evidence'))
        if evidence.model is not model:raise Unsupported('Flow window alias is from another source')
        allowed=[]
        if kind=='const_window':
            for ts in model.lists:
                ms=matching(ts)
                for i,t in enumerate(ts):
                    if t.value!='const'or model.scope(t.start)is not parent:continue
                    j=i+1
                    while j+1<len(ts)and ts[j].kind=='id'and ts[j+1].value=='=':
                        z=j+2
                        while z<len(ts)and ts[z].value not in {',',';'}:z=ms[z]+1 if ts[z].value in {'(','[','{'}else z+1
                        if ts[j].value==name and [x.value for x in ts[j+2:z]]==['window']and evidence.start<=t.start and ts[z-1].end<=evidence.end:allowed.append(ts[j])
                        if z>=len(ts)or ts[z].value!=',':break
                        j=z+1
            if len(allowed)!=1:raise Unsupported('Flow window alias lacks its exact immutable const initializer')
        elif kind=='iife_parameter':
            invocation=self.evidence(spec.get('invocation_evidence'))
            if invocation.model is not model or parent is None or parent.symbol is not None:raise Unsupported('Flow root parameter must belong to its actual anonymous IIFE')
            matches=[]
            for ts in model.lists:
                ms=matching(ts)
                at=next((i for i,t in enumerate(ts)if t.start==parent.start),None)
                if at is None:continue
                if at<1 or ts[at-1].value!='('or ts[at].value!='function'or at+1>=len(ts)or ts[at+1].value!='(':continue
                pc=ms[at+1];args=split_tokens(ts[at+2:pc]);names=[x[0].value for x in args if len(x)==1 and x[0].kind=='id']
                if len(names)!=len(args)or names.count(name)!=1:continue
                end=next((j for j,t in enumerate(ts)if t.end==parent.end),None)
                if end is None or ts[end+1].value!=')'or ts[end+2].value!='(':continue
                close=ms[end+2];actual=split_tokens(ts[end+3:close]);index=names.index(name)
                if len(actual)!=len(names)or [t.value for t in actual[index]]!=['window']:continue
                if not(evidence.start<=ts[at].start and ts[pc].end<=evidence.end and invocation.start<=ts[end+2].start and ts[close].end<=invocation.end):continue
                matches.append(actual[index][0]);allowed.append(args[index][0])
            if len(matches)!=1:raise Unsupported('Flow root is not the actual parameter of a directly invoked window IIFE')
            # A parameter initializer is not an assignment token. Any later
            # source assignment to the root remains forbidden.
        else:raise Unsupported('Unsupported explicit flow root alias kind')
        for ts in model.lists:
            for i,t in enumerate(ts):
                if t.value!=name or any(t is a for a in allowed)or i and ts[i-1].value in {'.','?.'}:continue
                p=path_at(ts,i,False)
                if p and p[0]==(name,):raise Unsupported('Flow window root is rebound, copied, forwarded or shadowed')
        self.no_shadow(model,'window',None,[evidence.start],include_parent=True)
        return name,('window',)

    def p4_flow_publication_root_unshadowed(self,model,name,position):
        # New opt-in publication only: parse declaration patterns, never scan
        # past a for-of/in header into unrelated following source identifiers.
        scopes=[];scope=model.enclosing_function(position)
        while True:
            scopes.append(scope)
            if scope is None:break
            scope=scope.parent
        def pattern_has(tokens):
            if not tokens:return False
            if tokens[0].value=='...':tokens=tokens[1:]
            if not tokens:return False
            if tokens[0].kind=='id':return tokens[0].value==name
            if tokens[0].value in {'[','{'}:
                end=matching(tokens).get(0)
                if end is None:raise Unsupported('Publication lexical pattern is malformed')
                return any(t.kind=='id'and t.value==name for t in tokens[1:end])
            raise Unsupported('Publication lexical declaration pattern is unsupported')
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts):
                owner=model.enclosing_function(t.start)
                if t.value==name and owner in scopes and not(i and ts[i-1].value in {'.','?.'}):
                    at=i+1
                    while at<len(ts)and ts[at].value==')':at+=1
                    if at<len(ts)and ts[at].value in {'=','+=','-=','*=','/=','++','--'}or i and ts[i-1].value in {'delete','++','--'}:raise Unsupported('Flow publication global root is assigned or deleted')
                if t.value in {'function','class'}and owner in scopes and i+1<len(ts)and ts[i+1].value==name:raise Unsupported('Flow publication global root is shadowed by a function declaration')
                if t.value in {'const','let','var'}and owner in scopes:
                    at=i+1
                    while at<len(ts):
                        if ts[at].value in {'[','{'}:
                            close=ms[at];pattern=ts[at:close+1];at=close+1
                        elif ts[at].kind=='id':pattern=ts[at:at+1];at+=1
                        else:raise Unsupported('Flow publication declaration pattern is unsupported')
                        if pattern_has(pattern):raise Unsupported('Flow publication global root is shadowed by a lexical declaration')
                        if at>=len(ts)or ts[at].value in {';',')','of','in'}:break
                        if ts[at].value=='=':
                            at+=1
                            while at<len(ts)and ts[at].value not in {',',';',')'}:at=ms[at]+1 if ts[at].value in {'(','[','{'}else at+1
                        if at>=len(ts)or ts[at].value!=',':break
                        at+=1
            for fn in scopes:
                if fn is None:continue
                at=next((i for i,t in enumerate(ts)if t.start==fn.start),None)
                if at is None:continue
                j=at+1
                if ts[at].value=='function'and j<len(ts)and ts[j].kind=='id':j+=1
                if j<len(ts)and ts[j].value=='(':
                    for arg in split_tokens(ts[j+1:ms[j]]):
                        if arg and pattern_has(arg):raise Unsupported('Flow publication global root is shadowed by a parameter')

    def p4_flow_export(self,spec):
        if not isinstance(spec,dict):raise Unsupported('Missing flow static publication evidence')
        e=self.evidence(spec.get('evidence'));callee=normalized_callee(spec.get('callee'));symbol=spec.get('symbol')
        if spec.get('version')!='bounded-static-publication@1':
            fn,parent,props,pos=self.explicit_owner_export(e,spec)
            canonical=normalized_callee(spec.get('global_owner_binding',{}).get('window_owner',''))
            return dict(function=fn,parent=parent,properties=props,position=pos,model=e.model,callee=callee,canonical_callee=(canonical+(callee[-1],)if canonical else callee),evidence=e)
        if not callee or len(callee)<2 or not IDENT.fullmatch(symbol or ''):raise Unsupported('Invalid flow publication target')
        model=e.model;keymap={};aliases={};parent=model.enclosing_function(e.start)
        # Evidence may start at line indentation before its containing IIFE.
        pubs=[model.scope(t.start)for ts in model.lists for i,t in enumerate(ts[:-1])if e.start<=t.start<e.end and t.kind=='id'and ts[i+1].value in {'.','['}]
        if parent is None and pubs:parent=pubs[0]
        for item in spec.get('constant_keys',[]):
            name,value=self.p4_flow_const_literal(item,model,parent)
            if name in keymap:raise Unsupported('Duplicate flow constant key')
            keymap[name]=value
        for item in spec.get('publication_aliases',[]):
            name,value=self.p4_flow_root_alias(item,model,parent)
            if name in aliases:raise Unsupported('Duplicate flow root alias')
            aliases[name]=value
        expected=callee
        bare=spec.get('bare_global_owner')
        if bare is not None:
            if not IDENT.fullmatch(bare or '')or callee[:-1]!=(bare,):raise Unsupported('Flow bare owner does not match actual event receiver')
            expected=('window',)+callee;self.no_shadow(model,bare,None,[e.start],include_parent=True)
        if expected[0]=='window':self.p4_flow_publication_root_unshadowed(model,'window',e.start)
        object_data=self.p4_flow_publication_object(spec['publication_object_binding'],model,parent,keymap,aliases,expected[:-1])if 'publication_object_binding'in spec else None
        extension_uses=[]
        for extension in spec.get('static_extensions',[]):
            ex=self.evidence(extension.get('evidence'))
            if ex.model is not model:raise Unsupported('Static publication extension is from another source')
            cs=[c for c in model.calls if ex.contains(c)and c.path==('Object','assign')and len(c.args)==2]
            if len(cs)!=1:raise Unsupported('Static extension requires one exact original Object.assign call')
            call=cs[0];receiver=self.p4_flow_path(call.args[0],0,keymap,aliases)
            if not receiver or receiver[1]!=len(call.args[0])or receiver[0]!=expected[:-1]:raise Unsupported('Static extension receiver differs from published owner')
            obj=call.args[1]
            if not obj or obj[0].value!='{'or matching(obj).get(0)!=len(obj)-1:raise Unsupported('Static extension requires literal non-target members')
            keys=[]
            for part in split_tokens(obj[1:-1]):
                if not part:continue
                key=part[0].value if part[0].kind=='id'else literal(part[0])
                if key is None or key in keys or key in {expected[-1],'__proto__'}or part[0].value in {'...','['}:raise Unsupported('Static extension can replace selected method or has an unknown key')
                if not(len(part)==1 and part[0].kind=='id'or len(part)==3 and part[1].value==':'and part[2].kind=='id'):raise Unsupported('Static extension value has an executable/computed member')
                keys.append(key)
            self.no_shadow(model,'Object',None,[call.start],include_parent=True)
            extension_uses.append(call.args[0][0])
        forwarding_tokens=[];forwarding_lhs=[];forwarded=[]
        for forwarding in spec.get('owner_forwardings',[]):
            fe=self.evidence(forwarding.get('evidence'));target=normalized_callee(forwarding.get('target'))
            if fe.model is not model or not target or target==expected[:-1]or target in forwarded:raise Unsupported('Invalid bounded static owner forwarding')
            matches=[]
            for ts in model.lists:
                for i,t in enumerate(ts):
                    if t.kind!='id'or i and ts[i-1].value in {'.','?.'}:continue
                    left=self.p4_flow_path(ts,i,keymap,aliases)
                    if not left or left[0]!=target or left[1]>=len(ts)or ts[left[1]].value!='=':continue
                    rhs=self.p4_flow_path(ts,left[1]+1,keymap,aliases)
                    if rhs and rhs[0]==expected[:-1]and rhs[1]<len(ts)and ts[rhs[1]].value==';'and fe.start<=t.start and ts[rhs[1]].end<=fe.end and model.scope(t.start)is parent:matches.append(ts[left[1]+1]);forwarding_lhs.append(t)
            if len(matches)!=1:raise Unsupported('Owner forwarding is not one exact original global alias assignment')
            forwarding_tokens+=matches;forwarded.append(target)
        if object_data:
            forwarded += [p for p in object_data['targets']if p!=expected[:-1]];forwarding_lhs += [t for t in object_data['lhs']if self.p4_flow_path(next(ts for ts in model.lists if t in ts),next(ts for ts in model.lists if t in ts).index(t),keymap,aliases)[0]!=expected[:-1]]
        found=[];assignments=[]
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts):
                if t.kind!='id'or i and ts[i-1].value in {'.','?.'}:continue
                parsed=self.p4_flow_path(ts,i,keymap,aliases)
                if not parsed:continue
                actual,j=parsed
                if actual==expected[:-1]and j<len(ts)and ts[j].value=='=':
                    assignments.append(t)
                    k=j+1;definition=e
                    if object_data and k+1<len(ts)and ts[k].value==object_data['symbol']and ts[k+1].value==';':
                        ot=object_data['tokens'];definition=object_data['evidence']
                        if not(e.start<=t.start and ts[k+1].end<=e.end):continue
                    else:
                        if k+3<len(ts)and [x.value for x in ts[k:k+4]]==['Object','.','freeze','(']:
                            self.no_shadow(model,'Object',None,[ts[k].start],include_parent=True);k+=4
                        if k>=len(ts)or ts[k].value!='{':continue
                        end=ms[k]
                        if not(e.start<=t.start and ts[end].end<=e.end):continue
                        ot=ts[k:end+1]
                    props={};inline_fn=None
                    for part in split_tokens(ot[1:-1]):
                        if not part:continue
                        name=part[0].value if part[0].kind=='id'else literal(part[0])
                        if not name or name in props or name=='__proto__'or part[0].value in {'...','['}:raise Unsupported('Flow publication has unknown/spread/duplicate properties')
                        if len(part)==1 and part[0].kind=='id':props[name]=name
                        elif len(part)==3 and part[1].value==':'and part[2].kind=='id':props[name]=part[2].value
                        elif len(part)>1 and part[1].value in {':','('}:
                            props[name]=None
                            if spec.get('inline_method')is True and name==expected[-1]and name==symbol and part[1].value=='(':
                                local=[f for f in model.functions if f.start==part[0].start and f.symbol is None and f.parent is model.scope(t.start)]
                                if len(local)!=1 or not(definition.start<=local[0].start and local[0].end<=definition.end):raise Unsupported('Flow inline method is not its exact published original body')
                                inline_fn=local[0];props[name]=symbol
                            if spec.get('inline_function')is True and name==expected[-1]and name==symbol and len(part)>4 and[x.value for x in part[1:4]]==[':','function','(']:
                                if spec.get('inline_method')is True:raise Unsupported('Flow anonymous function and shorthand method flags are mutually exclusive')
                                local=[f for f in model.functions if f.start==part[2].start and f.symbol is None and f.parent is model.scope(t.start)]
                                if len(local)!=1 or not(definition.start<=local[0].start and local[0].end<=definition.end)or local[0].end!=part[-1].end:raise Unsupported('Flow anonymous function property is not its exact full published body')
                                inline_fn=local[0];props[name]=symbol
                        elif part[0].value in {'get','set','async'}and len(part)>2 and part[1].kind=='id':
                            accessor=part[1].value
                            if accessor in props:raise Unsupported('Duplicate flow accessor property')
                            props[accessor]=None;continue
                        else:raise Unsupported('Flow publication member shape unsupported')
                    if props.get(expected[-1])!=symbol:continue
                    scope=model.scope(t.start);fn=inline_fn or model.declaration(symbol,scope)
                    if fn is None or fn.parent is not scope:raise Unsupported('Flow publication lacks exact same-scope named implementation')
                    found.append((fn,scope,props,t.start))
                # No metadata can permit target/owner rewrites, deletions,
                # whole-owner copies or unknown computed target writes.
                if actual==expected:
                    self.p4_no_write_contexts(model,{t.value},t.start-1,t.end+1)
                if actual==expected[:-1]and not any(t.start==item[3]for item in found):
                    self.p4_no_write_contexts(model,{t.value},t.start-1,t.end+1)
                if actual[:len(expected[:-1])]==expected[:-1]:
                    mutation=j<len(ts)and ts[j].value in {'=','+=','-=','++','--'}or i and ts[i-1].value=='delete'
                    if actual==expected and mutation:raise Unsupported('Flow published operation is rewritten')
                    if j<len(ts)and ts[j].value=='[':raise Unsupported('Flow published owner has an unknown computed access')
                    if actual==expected[:-1]and not mutation and not(j<len(ts)and ts[j].value in {'.','['})and not any(t is a for a in forwarding_tokens+extension_uses):raise Unsupported('Flow owner escapes through a copy/forward value')
                for forwarded_owner in forwarded:
                    if actual[:len(forwarded_owner)]==forwarded_owner:
                        if actual==forwarded_owner+(expected[-1],):self.p4_no_write_contexts(model,{t.value},t.start-1,t.end+1)
                        if actual==forwarded_owner and not any(t is a for a in forwarding_lhs):self.p4_no_write_contexts(model,{t.value},t.start-1,t.end+1)
                        if actual==forwarded_owner+(expected[-1],)and(j<len(ts)and ts[j].value in {'=','+=','-=','++','--'}or i and ts[i-1].value=='delete'):raise Unsupported('Forwarded published method is rewritten')
                        if actual==forwarded_owner and j<len(ts)and ts[j].value=='=' and not any(ts[j+1]is a for a in forwarding_tokens+(object_data['allowed']if object_data else[])):raise Unsupported('Forwarded owner is replaced')
                        if actual==forwarded_owner and not(j<len(ts)and ts[j].value in {'=','.', '['}):raise Unsupported('Forwarded owner has an unproven copy/forward escape')
                if t.value==symbol and i+1<len(ts)and ts[i+1].value in {'=','+=','-=','++','--'}and self.p4_flow_same_binding(model,symbol,t.start,parent):raise Unsupported('Flow implementation symbol is rebound')
        if len(found)!=1 or len(assignments)!=1:raise Unsupported('Flow requires one exact static publication')
        fn,parent,props,pos=found[0]
        return dict(function=fn,parent=parent,properties=props,position=pos,model=model,callee=callee,canonical_callee=expected,evidence=e,forwarded_owners=forwarded,implementation_symbol=symbol)

    def p4_flow_ui_alias(self,evidence,alias,parent,use):
        e=self.evidence(evidence);model=e.model
        if not IDENT.fullmatch(alias or ''):raise Unsupported('Flow UI alias is unnamed')
        aliases={}
        for item in evidence.get('publication_aliases',[]):
            name,value=self.p4_flow_root_alias(item,model,parent);aliases[name]=value
        found=[]
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts):
                if t.value not in {'const','var'}or model.scope(t.start)is not parent:continue
                j=i+1
                while j+1<len(ts)and ts[j].kind=='id'and ts[j+1].value=='=':
                    z=j+2
                    while z<len(ts)and ts[z].value not in {',',';'}:z=ms[z]+1 if ts[z].value in {'(','[','{'}else z+1
                    parsed=self.p4_flow_path(ts,j+2,{},aliases)
                    if ts[j].value==alias and parsed and parsed[1]==z and parsed[0]in {('PlatformUI',),('window','PlatformUI')}and e.start<=t.start and ts[z-1].end<=e.end:found.append(ts[j])
                    if z>=len(ts)or ts[z].value!=',':break
                    j=z+1
        if len(found)!=1:raise Unsupported('Flow UI alias lacks a unique actual PlatformUI initializer')
        self.p2_no_owner_writes(model,[(alias,)],found);self.no_shadow(model,alias,parent,[use])
        # Forbid object copies/forwarding, not normal static member reads.
        for ts in model.lists:
            for i,t in enumerate(ts):
                if t.value!=alias or t is found[0]or i and ts[i-1].value in {'.','?.'}:continue
                p=path_at(ts,i,False)
                if p and p[0]==(alias,)and not(p[1]<len(ts)and ts[p[1]].value in {'.','['}):raise Unsupported('Flow UI receiver escapes or is copied')
        return alias

    def p4_flow_ui_slot(self,spec):
        if not isinstance(spec,dict):raise Unsupported('Missing real UI HTML slot evidence')
        name=spec.get('helper_symbol');index=spec.get('argument_index');param=spec.get('parameter')
        if name not in {'pageHeader','toolbar','detailSection','openLayer'}or type(index)is not int or index<0 or not IDENT.fullmatch(param or ''):raise Unsupported('Unsupported bounded UI HTML slot')
        impl=self.evidence(spec.get('implementation_evidence'));export=self.evidence(spec.get('export_evidence'));fn,parent,props,pos=self.owner_export(export,'window.PlatformUI.'+name,name)
        if impl.model is not export.model:raise Unsupported('UI slot implementation/export disconnected')
        fn,ps,body=self.p4_flow_function(impl,name,parent)
        if index>=len(ps)or ps[index]!=param:raise Unsupported('UI HTML parameter/index differs from actual implementation')
        if name=='openLayer':
            se=self.evidence(spec.get('sink_evidence'));found=[]
            for ts in impl.model.lists:
                for i,t in enumerate(ts[:-4]):
                    if [x.value for x in ts[i:i+4]]!=['node','.','innerHTML','=']or ts[i+4].kind!='template'or impl.model.enclosing_function(t.start)is not fn:continue
                    template=ts[i+4]
                    if not(se.start<=t.start and template.end<=se.end):continue
                    if not any(k=='expression'and [x.value for x in part]==[param]and self.p1_active_part(template,part)for k,part in template.parts):continue
                    creates=[c for c in impl.model.calls if c.path==('document','createElement')and c.start<t.start and impl.model.enclosing_function(c.start)is fn and c.index>=3 and [x.value for x in c.tokens[c.index-3:c.index]]==['const','node','=']and len(c.args)==1 and self.p1_literal(c.args[0])=='div']
                    appends=[c for c in impl.model.calls if c.path==('document','body','appendChild')and c.start>template.end and impl.model.enclosing_function(c.start)is fn and len(c.args)==1 and [x.value for x in c.args[0]]==['node']]
                    if len(creates)==1 and len(appends)==1:found.append(template)
            if len(found)!=1:raise Unsupported('Real openLayer does not insert exact active HTML into its created/appended node')
        else:
            templates=self.p1_returns(impl.model,fn,body);self.p1_single_top_return(impl.model,fn,templates);hits=[]
            for template in templates:
                for kind,part in template.parts:
                    if kind=='expression'and [x.value for x in part]in [[param],[param,'||',"''"],[param,'||','""']]and self.p1_active_part(template,part):hits.append(part)
            if len(hits)!=1:raise Unsupported('Real UI helper does not return its HTML parameter in active content')
            uses=[t for ts in impl.model.lists for t in ts if t.value==param and fn.body_start<t.start<fn.body_end and impl.model.enclosing_function(t.start)is fn]
            if len(uses)!=1:raise Unsupported('Real UI HTML parameter has an extra rewrite/forward use')
        return dict(function=fn,model=impl.model,parameter=param,index=index)

    def p4_flow_factory(self,spec):
        if not isinstance(spec,dict)or not IDENT.fullmatch(spec.get('factory_symbol','')):raise Unsupported('Flow factory is unnamed')
        e=self.evidence(spec.get('evidence'));name=spec['factory_symbol'];pub=self.p4_flow_export(spec.get('publication'));owner=normalized_callee(spec.get('owner'))
        if not owner:raise Unsupported('Flow factory has no exact static published receiver')
        aliases={}
        for item in spec.get('publication_aliases',[]):
            alias,target=self.p4_flow_root_alias(item,e.model,e.model.scope(e.start));aliases[alias]=target
        candidates=[]
        for ts in e.model.lists:
            for i,t in enumerate(ts[:-6]):
                if [x.value for x in ts[i:i+6]]!=['const',name,'=','(',')','=>']:continue
                p=self.p4_flow_path(ts,i+6,{},aliases)
                if p and p[0]==owner and p[1]<len(ts)and ts[p[1]].value==';'and e.start<=t.start and ts[p[1]].end<=e.end:candidates.append(t)
        if len(candidates)!=1:raise Unsupported('Flow factory is not the original zero-argument const arrow returning its exact owner')
        parent=e.model.scope(candidates[0].start);self.p2_no_owner_writes(e.model,[(name,)] ,[next(ts[i+1]for ts in e.model.lists for i,t in enumerate(ts[:-1])if t is candidates[0])])
        if pub['canonical_callee'][:-1]!=owner and pub['canonical_callee'][:-1]!=('window',)+owner:raise Unsupported('Flow factory points to another published receiver')
        return dict(symbol=name,owner=owner,model=e.model,parent=parent,evidence=e,publication=pub)
    def p4_flow_function(self,e,symbol,parent=None):
        fn=e.model.declaration(symbol,parent)
        if fn is None and parent is None:
            fs=[f for f in e.model.functions if f.symbol==symbol and f.is_declaration]
            if len(fs)==1:fn=fs[0]
        if fn is None or not e.contains(fn):raise Unsupported('Flow function evidence lacks exact complete unique implementation: '+str(symbol))
        for ts in e.model.lists:
            at=next((i for i,t in enumerate(ts)if t.start==fn.start),None)
            if at is None:continue
            ms=matching(ts);j=at+2
            if j>=len(ts)or ts[j].value!='(':raise Unsupported('Flow named function parameters unsupported')
            close=ms[j];params=split_tokens(ts[j+1:close]);names=[]
            for p in params:
                if not p:continue
                if p[0].kind!='id':raise Unsupported('Flow function has destructured/computed parameter')
                if len(p)>1:
                    if p[1].value!='=':raise Unsupported('Flow function parameter shape unsupported')
                    d=p[2:];ok=len(d)==1 and (d[0].kind in {'string','number'}or d[0].value in {'true','false','null'})or [x.value for x in d]in [['{','}'],['[',']']]
                    if not ok:raise Unsupported('Flow default parameter must be an original literal')
                names.append(p[0].value)
            return fn,names,ts[close+2:ms[close+1]]
        raise Unsupported('Flow implementation tokens missing')

    def p4_flow_route(self,spec):
        if not isinstance(spec,dict)or spec.get('route_options_policy')!='default_route_view_no_override':raise Unsupported('Flow route must preserve original default view policy')
        for key in ['route_key','route_page']:
            if not isinstance(spec.get(key),str)or not spec[key]:raise Unsupported('Flow route lacks exact '+key)
        ev=spec.get('evidence',{});kind=spec.get('registration_kind','menu_literal');rp=dict(spec)
        if kind=='menu_literal':view=self.p4_route_menu(rp,ev,('window','Pages',spec['route_page'],'render'))if spec.get('route_decorations')or spec.get('resolver_context')else self.p1_route(rp,ev,('window','Pages',spec['route_page'],'render'))
        elif kind=='literal_map_property':view=self.p4_route_property(rp,ev,('window','Pages',spec['route_page'],'render'))
        else:raise Unsupported('Unsupported finite flow route registration')
        e=self.evidence(ev.get('route_registration'))
        return dict(model=e.model,route_key=spec['route_key'],route_page=spec['route_page'],route_view=view,registration_kind=kind,context_proof=getattr(self,'_p4_verified_role_context',None)if spec.get('resolver_context')else None)

    def p4_property_map_builder(self,model,builder,array_start,route_key,mapped,route_map):
        # Prove the generic menu projection cannot overwrite this separately
        # registered internal literal. No menu link is borrowed as its UI.
        children=[];allkeys=[]
        for ts in model.lists:
            ms=matching(ts);at=next((i for i,t in enumerate(ts)if t.start==array_start),None)
            if at is None:continue
            for item in split_tokens(ts[at+1:ms[at]]):
                if not item:continue
                props=self.p1_props(item);key=self.p1_literal(props.get('key',[]))
                if key is None:raise Unsupported('Internal route menu projection contains unknown top-level key')
                allkeys.append(key);cs=props.get('children')
                if cs is None:continue
                if not cs or cs[0].value!='['or matching(cs).get(0)!=len(cs)-1:raise Unsupported('Internal route projection children must be literal')
                for child in split_tokens(cs[1:-1]):
                    if not child:continue
                    p=self.p1_props(child)
                    if self.p1_literal(p.get('type',[]))=='section':continue
                    ck=self.p1_literal(p.get('key',[]))
                    if ck is None:raise Unsupported('Internal route projection contains an unknown child key')
                    allkeys.append(ck);children.append(child[0].start)
        if not children or len(allkeys)!=len(set(allkeys))or route_key in allkeys:raise Unsupported('Internal literal route collides with original menu projection')
        self.p1_child_projection(model,builder,array_start,children[0],mapped)
        group=builder.args[0][0].value;valid_positions={pos for pos,item in mapped}
        # Top-level menu entries may also be projected; keep their exact
        # original spread/page-fallback shape and actual callback parameter.
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts):
                if t.value!=route_map or not(builder.start<t.start<builder.end)or i+1>=len(ts)or ts[i+1].value!='[':continue
                close=ms[i+1];j=close+1
                if j>=len(ts)or ts[j].value!='=':continue
                if t.start in valid_positions:continue
                if [x.value for x in ts[i+2:close]]!=[group,'.','key']or j+1>=len(ts)or ts[j+1].value!='{':raise Unsupported('Unknown dynamic writer can replace internal literal route')
                props=split_tokens(ts[j+2:ms[j+1]])
                if [x.value for x in props[0]]!=['...',group]:raise Unsupported('Top-level menu route drops original literal identity')
                expected={'page':[group,'.','page','||',group,'.','key'],'breadcrumb':['[',group,'.','label',']']}
                for part in props[1:]:
                    if len(part)<3 or part[1].value!=':'or part[0].value not in expected or [x.value for x in part[2:]]!=expected[part[0].value]:raise Unsupported('Top-level route projection has an unproven override')

    def p4_flow_checked_endpoint(self,e,symbol,result,flow):
        try:return self.p4_flow_function(e,symbol)
        except Unsupported:
            op=flow.get('entry_binding',{});bound=result.get('verified_default_endpoint')
            if flow.get('family')!='execution'or op.get('version')!='bounded-field-kind-flow@1'or op.get('operation',op.get('profile_kind'))!='query'or not isinstance(bound,dict):raise
            fn=bound.get('function');model=bound.get('model');selector=bound.get('selector_function');binding=bound.get('state_binding_token');be=bound.get('evidence')
            if not isinstance(fn,Function)or model is not e.model or not isinstance(be,Evidence)or be.model is not model or not be.contains(fn)or not e.contains(fn)or fn.symbol!=symbol or symbol!='render'or not isinstance(selector,Function)or selector.symbol!='selectKind'or selector.parent is not fn.parent or not any(x is selector for x in model.functions)or not isinstance(binding,Token)or binding.value!='activeKind':raise Unsupported('Field dynamic default endpoint lacks its actual verified lexical functions/state binding')
            parsed=self.p4_execution_named(dict(symbol=symbol,evidence=e.record))
            if parsed['function']is not fn or len(parsed['params'])!=1 or[x.value for x in parsed['params'][0]]!=['kind','=','activeKind']:raise Unsupported('Field verified default differs from exact original render(kind=activeKind)')
            declarations=[]
            for ts in model.lists:
                ms=matching(ts)
                for i,t in enumerate(ts[:-3]):
                    if t.value!='let'or model.enclosing_function(t.start)is not fn.parent:continue
                    j=i+1
                    while j+2<len(ts)and ts[j].kind=='id'and ts[j+1].value=='=':
                        z=j+2
                        while z<len(ts)and ts[z].value not in {',',';'}:z=ms[z]+1 if ts[z].value in {'(','[','{'}else z+1
                        if ts[j]is binding and z==j+3 and literal(ts[j+2])=='category':declarations.append(t)
                        if z>=len(ts)or ts[z].value!=',':break
                        j=z+1
            if len(declarations)!=1:raise Unsupported('Field dynamic default is not its original same-scope let activeKind binding')
            return fn,['kind'],parsed['body']

    def validate_active_flow(self,require_followup=False):
        chain=self.proof.get('source_chain',{});flow=chain.get('source_flow_provenance')
        if chain.get('version')!='bounded-active-source-chain@1'or not isinstance(flow,dict)or flow.get('version')!='bounded-active-source-flow@1'or flow.get('reachability')!='conditional_only':raise Unsupported('Unsupported explicit active source-flow version')
        if not isinstance(flow.get('flow_steps'),list)or not flow['flow_steps']or len(flow['flow_steps'])>32:raise Unsupported('Active flow requires a bounded explicit source-edge list')
        entry=chain.get('entry',{})
        if entry.get('event')not in EVENTS or not normalized_callee(entry.get('callee')):raise Unsupported('Active flow lacks exact original event/callee')
        ee=self.evidence(entry.get('evidence'));family=flow.get('family');method={'resource':'p4_resource_flow','reporting':'p4_reporting_flow','execution':'p4_execution_flow'}.get(family)
        if family=='resource'and isinstance(flow.get('entry_binding'),dict)and flow['entry_binding'].get('kind')=='inbound_rule':method='p4_inbound_flow'
        decoder=getattr(self,method,None)if method else None
        if not callable(decoder):raise Unsupported('Finite active source-flow family is not implemented: '+str(family))
        result=decoder(flow)
        if not isinstance(result,dict):raise Unsupported('Active flow decoder did not return actual bound source endpoints')
        original=chain.get('terminal',{});te=self.evidence(original.get('evidence'));symbol=original.get('symbol')
        if symbol!=self.proof.get('target_symbol')or symbol!=result.get('terminal_symbol')or te.model.path!=self.candidate_path:raise Unsupported('Active flow terminal differs from exact original candidate implementation')
        actual=result.get('terminal_evidence');start=result.get('entry_evidence');outcome=result.get('result_evidence')
        if not all(isinstance(x,Evidence)for x in [actual,start,outcome])or actual.model is not te.model or not(te.start<=actual.start and actual.end<=te.end)or start.model is not ee.model or(start.start,start.end)!=(ee.start,ee.end):raise Unsupported('Active flow endpoints are disconnected from actual original entry/terminal')
        fn,params,body=self.p4_flow_checked_endpoint(te,symbol,result,flow)
        if not actual.contains(fn):raise Unsupported('Active flow terminal evidence does not cover the full original implementation')
        result_symbol=result.get('result_symbol')
        if not IDENT.fullmatch(result_symbol or ''):raise Unsupported('Active flow actual result implementation is missing')
        rfn,rp,rb=self.p4_flow_checked_endpoint(outcome,result_symbol,result,flow)
        role=self.proof.get('original_anchor',{}).get('role')
        if result.get('followup_verified')is True and role!='initiator_only_needs_save_result':raise Unsupported('User-save result needs its explicit original initiator role contract')
        require_followup=require_followup or role=='initiator_only_needs_save_result'
        if require_followup and (result.get('followup_verified')is not True or rfn is fn):raise Unsupported('Original initiator lacks independently proven user save and actual result')
        return result

    def p4_route_property(self,rp,ev,callee):
        registration=self.evidence(ev['route_registration']);dispatch=self.evidence(ev['route_dispatch']);sink=self.evidence(ev['route_sink'])
        if not(registration.model is dispatch.model is sink.model):raise Unsupported('Route registration/dispatch/DOM sink must share the exact source')
        model=registration.model;route_key=rp['route_key'];route_page=rp['route_page'];route_view=rp.get('route_view');property_write_start=None;map_name=rp.get('route_map_symbol','routes');menu_name=rp.get('menu_symbol','menu')
        if not IDENT.fullmatch(map_name or '')or not IDENT.fullmatch(menu_name or ''):raise Unsupported('Internal route map/menu symbols must be exact identifiers')
        if callee[:-1] not in {('window','Pages',route_page),('Pages',route_page)}:raise Unsupported('Route page is not the exact event owner')
        selected=[];arrays=[]
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts):
                if t.value=='const' and i+3<len(ts) and ts[i+1].kind=='id' and ts[i+2].value=='=' and ts[i+3].value=='[':
                    arrays.append((ts[i+1].value,ts[i+3].start,ts[ms[i+3]].end,model.scope(t.start)))
                if t.value!='{' or not(registration.start<=t.start and ts[ms[i]].end<=registration.end):continue
                try:props=self.p1_props(ts[i:ms[i]+1])
                except Unsupported:continue
                if i>=5 and [x.value for x in ts[i-5:i-3]]==[map_name,'[']and self.p1_literal(ts[i-3:i-2])==route_key and [x.value for x in ts[i-2:i]]==[']','=']:
                    if 'key'in props and self.p1_literal(props['key'])!=route_key:raise Unsupported('Internal route key property differs from literal map slot')
                    selected.append((t.start,ts[ms[i]].end,props));property_write_start=ts[i-5].start
        if len(selected)!=1:raise Unsupported('Route provenance needs one exact registered literal route key')
        start,end,props=selected[0]
        if self.p1_literal(props.get('page',[]))!=route_page or self.p1_literal(props.get('view',[]))!=route_view or 'redirect' in props:raise Unsupported('Route page/view differs or redirects the selected route')
        containers=[a for a in arrays if a[0]==menu_name]
        if len(containers)!=1:raise Unsupported('Internal route requires its unique original literal menu projector')
        collection,array_start,_,parent=containers[0]
        if property_write_start is None or model.scope(property_write_start)is not parent:raise Unsupported('Internal route property is outside original route-map scope')
        map_declarations=[t for ts in model.lists for i,t in enumerate(ts[:-4])if [x.value for x in ts[i:i+5]]==['const',map_name,'=','{','}']and model.scope(t.start)is parent]
        if len(map_declarations)!=1:raise Unsupported('Internal route map lacks its unique original literal object initializer')
        self.p1_collection_stable(model,collection,array_start,parent)
        # Bind the real dispatch variables, rather than trusting a route label.
        fragments=[]
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts):
                if not(dispatch.start<=t.start<dispatch.end):continue
                if [x.value for x in ts[i:i+6]]==['const','page','=','window','.','Pages'] and i+11<len(ts) and [x.value for x in ts[i+6:i+12]]==['[','route','.','page',']',';']:
                    fragments.append(t)
        if len(fragments)!=1:raise Unsupported('Route dispatch lacks its exact window.Pages[route.page] binding')
        navigation=model.enclosing_function(fragments[0].start)
        if navigation is None:raise Unsupported('Route dispatch is outside its published navigator')
        publications=[]
        for ts in model.lists:
            for i,t in enumerate(ts):
                p=path_at(ts,i,False)
                if not p or p[0]!=('window','navigateTo'):continue
                j=p[1]
                if j<len(ts) and ts[j].value in {'=','+=','-=','++','--'}:
                    if ts[j].value!='=' or j+1>=len(ts) or ts[j+1].start!=navigation.start:raise Unsupported('Published navigator is rewritten or disconnected')
                    publications.append(t)
                if i and ts[i-1].value=='delete':raise Unsupported('Published navigator is deleted')
        if len(publications)!=1:raise Unsupported('Route dispatcher is not the unique actual window.navigateTo publication')
        self.no_shadow(model,'window',None,[publications[0].start],include_parent=True)
        # Models contain separate template lists; declarations are checked on
        # the one actual containing list, not by concatenating arbitrary text.
        direct=[]
        for ts in model.lists:
            for i,t in enumerate(ts):
                if not(dispatch.start<=t.start<dispatch.end):continue
                vals=[x.value for x in ts[i:i+5]]
                if vals==['const','route','=',collection,'.']:
                    cs=[c for c in model.calls if c.tokens is ts and c.index==i+3 and c.path==(collection,'find')]
                    for c in cs:
                        if len(c.args)!=1:continue
                        a=c.args[0];v=[x.value for x in a]
                        if len(v)==7 and a[0].kind=='id' and v[1:]==['=>',a[0].value,'.','key','===','key']:direct.append(c)
        if direct:
            if len(direct)!=1 or model.enclosing_function(direct[0].start) is not navigation:raise Unsupported('Route find is outside the exact navigator')
            self.p4_flow_selected_key_stable(model,navigation,'key',route_key,context_spec=rp.get('resolver_context'))
        else:
            resolution=self.evidence(ev.get('route_resolution'))
            if resolution.model is not model:raise Unsupported('Route resolver must be in the registered navigation source')
            # Finite menu-child projection used by the original navigation:
            # literal menu -> invoked forEach -> routes[item.key]={...item,...}.
            resolver_calls=[c for c in model.calls if dispatch.contains(c) and len(c.path)==1 and model.enclosing_function(c.start) is navigation]
            linked=[]
            for c in resolver_calls:
                if len(c.args)!=1 or [t.value for t in c.args[0]]!=['key']:continue
                fn=model.declaration(c.path[0],navigation.parent)
                if fn is None or not resolution.contains(fn):continue
                f,ps,body=self.p1_function(resolution,c.path[0],navigation.parent)
                values=[t.value for t in body]
                lookups=[]
                for i in range(len(body)-8):
                    if values[i:i+3]in [['let','route','='],['const','route','=']] and body[i+3].kind=='id' and values[i+4:i+7]==['[','key',']']:lookups.append(body[i+3].value)
                if len(lookups)!=1 or not any(values[i:i+6]==['return','{','key',',','route','}'] for i in range(len(values)-5)):continue
                self.p4_flow_selected_key_stable(model,f,'key',route_key,'route',context_spec=rp.get('resolver_context'))
                for z,t in enumerate(body[:-1]):
                    if t.value=='route' and body[z+1].value=='=' and (z==0 or body[z-1].value not in {'.','?.'}):
                        if [x.value for x in body[z+2:z+6]]!=[lookups[0],'[','key',']']:raise Unsupported('Resolver replaces the actual route object')
                    if t.value=='route' and body[z+1].value in {'.','['}:
                        bm=matching(body);at=z+1
                        while at<len(body) and body[at].value in {'.','['}:at=bm[at]+1 if body[at].value=='[' else at+2
                        if at<len(body) and body[at].value in {'=','+=','-=','++','--'}:raise Unsupported('Resolver rewrites the selected route property')
                # The resolver result actually supplies the dispatch route.
                if not any([t.value for t in ts[i:i+7]]==['const','route','=','resolved','.','route',';'] for ts in model.lists for i in range(len(ts)-6) if dispatch.start<=ts[i].start<dispatch.end):continue
                if not any([t.value for t in ts[i:i+3]]==['const','resolved','='] and i+3==c.index for ts in [c.tokens] for i in range(len(ts)-3)):continue
                self.p4_flow_selected_key_stable(model,navigation,'key',route_key,allowed_resolved_after=c.end,context_spec=rp.get('resolver_context'))
                linked.append(lookups[0])
            if len(linked)!=1:raise Unsupported('Route lookup is not bound to its exact named resolver')
            route_map=linked[0]
            builders=[c for c in model.calls if c.path==(collection,'forEach') and registration.contains(c) and model.enclosing_function(c.start) is parent]
            if len(builders)!=1:raise Unsupported('Registered menu is not projected by its actual invoked route builder')
            builder=builders[0];mapped=[]
            for ts in model.lists:
                ms=matching(ts)
                for i,t in enumerate(ts):
                    if t.value!=route_map or not(builder.start<t.start<builder.end):continue
                    if i+8>=len(ts) or ts[i+1].value!='[' or ts[i+2].kind!='id' or [x.value for x in ts[i+3:i+8]]!=['.','key',']','=','{']:continue
                    obj=ts[i+7:ms[i+7]+1];parts=split_tokens(obj[1:-1]);item=ts[i+2].value
                    if not parts or [x.value for x in parts[0]]!=['...',item]:continue
                    tail=parts[1:]
                    if any(not p or p[0].value in {'...','['} or len(p)<3 or p[1].value!=':' or p[0].value in {'page','view','key'} for p in tail):continue
                    mapped.append((t.start,item))
            if not mapped:raise Unsupported('Route builder does not preserve literal child page/view/key')
            self.p4_property_map_builder(model,builder,array_start,route_key,mapped,route_map)
            # Unknown writes to the selected map slot cannot borrow a stale
            # registration. Other literal slots do not replace this object.
            for ts in model.lists:
                ms=matching(ts)
                for i,t in enumerate(ts):
                    if t.value!=route_map or i+1>=len(ts) or ts[i+1].value!='[':continue
                    close=ms[i+1];j=close+1
                    while j+1<len(ts) and ts[j].value=='.':j+=2
                    if j<len(ts) and ts[j].value in {'=','+=','-=','++','--'}:
                        if builder.start<t.start<builder.end or t.start==property_write_start:continue
                        key=self.p1_literal(ts[i+2:close])
                        if key is None or key==route_key:raise Unsupported('Selected route map has an unproven later write')
        # Default view, exact page render, and a real DOM node sink are bound.
        self.p1_options_binding(dispatch,navigation)
        hits=[]
        for c in model.calls:
            if c.path!=('page','render') or not sink.contains(c) or model.enclosing_function(c.start) is not navigation or len(c.args)!=1 or [t.value for t in c.args[0]]!=['opts']:continue
            pre=[t.value for t in c.tokens[max(0,c.index-4):c.index]]
            if pre==['content','.','innerHTML','='] and c.end_index+1<len(c.tokens) and c.tokens[c.end_index+1].value==';':hits.append(c)
        if len(hits)!=1:raise Unsupported('Route render has no exact DOM innerHTML sink')
        self.p1_navigation_stable(model,navigation,fragments[0].start,hits[0])
        dom=False
        for c in model.calls:
            if c.path==('document','getElementById') and c.start<hits[0].start and model.enclosing_function(c.start) is navigation and c.index>=3 and [t.value for t in c.tokens[c.index-3:c.index]] in [['let','content','='],['const','content','=']] and c.end_index+1<len(c.tokens) and c.tokens[c.end_index+1].value==';':dom=True
        if not dom:raise Unsupported('Route content is not the original DOM element binding')
        self.no_shadow(model,collection,parent,[fragments[0].start])
        return route_view

    def p4_flow_body(self,binding):
        if not isinstance(binding,dict)or not isinstance(binding.get('function'),Function):raise Unsupported('Missing exact flow function binding')
        fn=binding['function'];model=binding['model']
        for ts in model.lists:
            at=next((i for i,t in enumerate(ts)if t.start==fn.start),None)
            if at is None:continue
            ms=matching(ts);j=at+1
            if ts[at].value=='function'and j<len(ts)and ts[j].kind=='id':j+=1
            if j>=len(ts)or ts[j].value!='(':raise Unsupported('Flow bound method parameters unavailable')
            end=ms[j];params=split_tokens(ts[j+1:end]);names=[]
            for p in params:
                if not p:continue
                if p[0].kind!='id'or len(p)>1 and(p[1].value!='='or not(len(p[2:])==1 and(p[2].kind in {'string','number'}or p[2].value in {'true','false','null'})or[x.value for x in p[2:]]in [['{','}'],['[',']']])):raise Unsupported('Flow bound method requires plain literal-default parameters')
                names.append(p[0].value)
            return fn,names,ts[end+2:ms[end+1]]
        raise Unsupported('Flow bound method exact body missing')

    def p4_route_decoration(self,model,ts,index,route_key,spec):
        ms=matching(ts);i=index
        if i+9>=len(ts)or ts[i+1].value!='[':return False
        close=ms[i+1]
        if self.p1_literal(ts[i+2:close])!=route_key or [t.value for t in ts[close+1:close+5]]!=['.','breadcrumb','=','[']:return False
        start=close+4;end=ms[start]
        if end+1>=len(ts)or ts[end+1].value!=';':return False
        values=split_tokens(ts[start+1:end])
        if any(len(v)!=1 or literal(v[0])is None for v in values):return False
        for record in spec.get('route_decorations',[]):
            if not isinstance(record,dict)or record.get('field')!='breadcrumb':raise Unsupported('Only explicitly evidenced literal breadcrumb decoration is supported')
            e=self.evidence(record.get('evidence'))
            if e.model is model and e.start<=ts[i].start and ts[end+1].end<=e.end:return True
        return False

    def p4_route_menu(self,rp,ev,callee):
        registration=self.evidence(ev['route_registration']);dispatch=self.evidence(ev['route_dispatch']);sink=self.evidence(ev['route_sink'])
        if not(registration.model is dispatch.model is sink.model):raise Unsupported('Route registration/dispatch/DOM sink must share the exact source')
        model=registration.model;route_key=rp['route_key'];route_page=rp['route_page'];route_view=rp['route_view']
        if callee[:-1] not in {('window','Pages',route_page),('Pages',route_page)}:raise Unsupported('Route page is not the exact event owner')
        selected=[];arrays=[]
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts):
                if t.value=='const' and i+3<len(ts) and ts[i+1].kind=='id' and ts[i+2].value=='=' and ts[i+3].value=='[':
                    arrays.append((ts[i+1].value,ts[i+3].start,ts[ms[i+3]].end,model.scope(t.start)))
                if t.value!='{' or not(registration.start<=t.start and ts[ms[i]].end<=registration.end):continue
                try:props=self.p1_props(ts[i:ms[i]+1])
                except Unsupported:continue
                if self.p1_literal(props.get('key',[]))==route_key:selected.append((t.start,ts[ms[i]].end,props))
        if len(selected)!=1:raise Unsupported('Route provenance needs one exact registered literal route key')
        start,end,props=selected[0]
        if self.p1_literal(props.get('page',[]))!=route_page or self.p1_literal(props.get('view',[]))!=route_view or 'redirect' in props:raise Unsupported('Route page/view differs or redirects the selected route')
        containers=[a for a in arrays if a[1]<start<end<a[2]]
        if not containers:raise Unsupported('Selected route is not in an actual literal registration array')
        collection,array_start,_,parent=min(containers,key=lambda a:a[2]-a[1])
        self.p1_collection_stable(model,collection,array_start,parent)
        # Bind the real dispatch variables, rather than trusting a route label.
        fragments=[]
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts):
                if not(dispatch.start<=t.start<dispatch.end):continue
                if [x.value for x in ts[i:i+6]]==['const','page','=','window','.','Pages'] and i+11<len(ts) and [x.value for x in ts[i+6:i+12]]==['[','route','.','page',']',';']:
                    fragments.append(t)
        if len(fragments)!=1:raise Unsupported('Route dispatch lacks its exact window.Pages[route.page] binding')
        navigation=model.enclosing_function(fragments[0].start)
        if navigation is None:raise Unsupported('Route dispatch is outside its published navigator')
        publications=[]
        for ts in model.lists:
            for i,t in enumerate(ts):
                p=path_at(ts,i,False)
                if not p or p[0]!=('window','navigateTo'):continue
                j=p[1]
                if j<len(ts) and ts[j].value in {'=','+=','-=','++','--'}:
                    if ts[j].value!='=' or j+1>=len(ts) or ts[j+1].start!=navigation.start:raise Unsupported('Published navigator is rewritten or disconnected')
                    publications.append(t)
                if i and ts[i-1].value=='delete':raise Unsupported('Published navigator is deleted')
        if len(publications)!=1:raise Unsupported('Route dispatcher is not the unique actual window.navigateTo publication')
        self.no_shadow(model,'window',None,[publications[0].start],include_parent=True)
        # Models contain separate template lists; declarations are checked on
        # the one actual containing list, not by concatenating arbitrary text.
        direct=[]
        for ts in model.lists:
            for i,t in enumerate(ts):
                if not(dispatch.start<=t.start<dispatch.end):continue
                vals=[x.value for x in ts[i:i+5]]
                if vals==['const','route','=',collection,'.']:
                    cs=[c for c in model.calls if c.tokens is ts and c.index==i+3 and c.path==(collection,'find')]
                    for c in cs:
                        if len(c.args)!=1:continue
                        a=c.args[0];v=[x.value for x in a]
                        if len(v)==7 and a[0].kind=='id' and v[1:]==['=>',a[0].value,'.','key','===','key']:direct.append(c)
        if direct:
            if len(direct)!=1 or model.enclosing_function(direct[0].start) is not navigation:raise Unsupported('Route find is outside the exact navigator')
            self.p4_flow_selected_key_stable(model,navigation,'key',route_key,context_spec=rp.get('resolver_context'))
        else:
            resolution=self.evidence(ev.get('route_resolution'))
            if resolution.model is not model:raise Unsupported('Route resolver must be in the registered navigation source')
            # Finite menu-child projection used by the original navigation:
            # literal menu -> invoked forEach -> routes[item.key]={...item,...}.
            resolver_calls=[c for c in model.calls if dispatch.contains(c) and len(c.path)==1 and model.enclosing_function(c.start) is navigation]
            linked=[]
            for c in resolver_calls:
                if len(c.args)!=1 or [t.value for t in c.args[0]]!=['key']:continue
                fn=model.declaration(c.path[0],navigation.parent)
                if fn is None or not resolution.contains(fn):continue
                f,ps,body=self.p1_function(resolution,c.path[0],navigation.parent)
                values=[t.value for t in body]
                lookups=[]
                for i in range(len(body)-8):
                    if values[i:i+3]in [['let','route','='],['const','route','=']] and body[i+3].kind=='id' and values[i+4:i+7]==['[','key',']']:lookups.append(body[i+3].value)
                if len(lookups)!=1 or not any(values[i:i+6]==['return','{','key',',','route','}'] for i in range(len(values)-5)):continue
                self.p4_flow_selected_key_stable(model,f,'key',route_key,'route',context_spec=rp.get('resolver_context'))
                for z,t in enumerate(body[:-1]):
                    if t.value=='route' and body[z+1].value=='=' and (z==0 or body[z-1].value not in {'.','?.'}):
                        if [x.value for x in body[z+2:z+6]]!=[lookups[0],'[','key',']']:raise Unsupported('Resolver replaces the actual route object')
                    if t.value=='route' and body[z+1].value in {'.','['}:
                        bm=matching(body);at=z+1
                        while at<len(body) and body[at].value in {'.','['}:at=bm[at]+1 if body[at].value=='[' else at+2
                        if at<len(body) and body[at].value in {'=','+=','-=','++','--'}:raise Unsupported('Resolver rewrites the selected route property')
                # The resolver result actually supplies the dispatch route.
                if not any([t.value for t in ts[i:i+7]]==['const','route','=','resolved','.','route',';'] for ts in model.lists for i in range(len(ts)-6) if dispatch.start<=ts[i].start<dispatch.end):continue
                if not any([t.value for t in ts[i:i+3]]==['const','resolved','='] and i+3==c.index for ts in [c.tokens] for i in range(len(ts)-3)):continue
                self.p4_flow_selected_key_stable(model,navigation,'key',route_key,allowed_resolved_after=c.end,context_spec=rp.get('resolver_context'))
                linked.append(lookups[0])
            if len(linked)!=1:raise Unsupported('Route lookup is not bound to its exact named resolver')
            route_map=linked[0]
            builders=[c for c in model.calls if c.path==(collection,'forEach') and registration.contains(c) and model.enclosing_function(c.start) is parent]
            if len(builders)!=1:raise Unsupported('Registered menu is not projected by its actual invoked route builder')
            builder=builders[0];mapped=[]
            for ts in model.lists:
                ms=matching(ts)
                for i,t in enumerate(ts):
                    if t.value!=route_map or not(builder.start<t.start<builder.end):continue
                    if i+8>=len(ts) or ts[i+1].value!='[' or ts[i+2].kind!='id' or [x.value for x in ts[i+3:i+8]]!=['.','key',']','=','{']:continue
                    obj=ts[i+7:ms[i+7]+1];parts=split_tokens(obj[1:-1]);item=ts[i+2].value
                    if not parts or [x.value for x in parts[0]]!=['...',item]:continue
                    tail=parts[1:]
                    if any(not p or p[0].value in {'...','['} or len(p)<3 or p[1].value!=':' or p[0].value in {'page','view','key'} for p in tail):continue
                    mapped.append((t.start,item))
            if not mapped:raise Unsupported('Route builder does not preserve literal child page/view/key')
            if not self.p4_top_level_leaf_projection(model,builder,array_start,start,route_map):
                self.p1_child_projection(model,builder,array_start,start,mapped)
            # Unknown writes to the selected map slot cannot borrow a stale
            # registration. Other literal slots do not replace this object.
            for ts in model.lists:
                ms=matching(ts)
                for i,t in enumerate(ts):
                    if t.value!=route_map or i+1>=len(ts) or ts[i+1].value!='[':continue
                    close=ms[i+1];j=close+1
                    while j+1<len(ts) and ts[j].value=='.':j+=2
                    if j<len(ts) and ts[j].value in {'=','+=','-=','++','--'}:
                        if builder.start<t.start<builder.end:continue
                        key=self.p1_literal(ts[i+2:close])
                        if key is None or key==route_key:
                            if key==route_key and self.p4_route_decoration(model,ts,i,route_key,rp):continue
                            raise Unsupported('Selected route map has an unproven later write')
        # Default view, exact page render, and a real DOM node sink are bound.
        self.p1_options_binding(dispatch,navigation)
        hits=[]
        for c in model.calls:
            if c.path!=('page','render') or not sink.contains(c) or model.enclosing_function(c.start) is not navigation or len(c.args)!=1 or [t.value for t in c.args[0]]!=['opts']:continue
            pre=[t.value for t in c.tokens[max(0,c.index-4):c.index]]
            if pre==['content','.','innerHTML','='] and c.end_index+1<len(c.tokens) and c.tokens[c.end_index+1].value==';':hits.append(c)
        if len(hits)!=1:raise Unsupported('Route render has no exact DOM innerHTML sink')
        self.p1_navigation_stable(model,navigation,fragments[0].start,hits[0])
        dom=False
        for c in model.calls:
            if c.path==('document','getElementById') and c.start<hits[0].start and model.enclosing_function(c.start) is navigation and c.index>=3 and [t.value for t in c.tokens[c.index-3:c.index]] in [['let','content','='],['const','content','=']] and c.end_index+1<len(c.tokens) and c.tokens[c.end_index+1].value==';':dom=True
        if not dom:raise Unsupported('Route content is not the original DOM element binding')
        self.no_shadow(model,collection,parent,[fragments[0].start])
        return route_view
    def p4_flow_same_binding(self,model,name,position,parent):
        # A named function's parameter initializer is lexically before its
        # body and Model.scope therefore sees the outer scope. Bind only the
        # actual parameter declaration token, not arbitrary default RHS uses.
        for fn in model.functions:
            if not(fn.start<position<fn.body_start):continue
            for ts in model.lists:
                at=next((i for i,t in enumerate(ts)if t.start==fn.start),None)
                if at is None:continue
                ms=matching(ts);j=at+1
                if ts[at].value=='function'and j<len(ts)and ts[j].kind=='id':j+=1
                if j<len(ts)and ts[j].value=='(':
                    for part in split_tokens(ts[j+1:ms[j]]):
                        if part and part[0].value==name and part[0].start==position:return False
        scope=model.scope(position)
        while scope is not parent:
            if scope is None:return False
            if any(bound==name and owner is scope for bound,pos,owner in model.variable_bindings()):return False
            if model.declaration(name,scope)is not None:return False
            for ts in model.lists:
                at=next((i for i,t in enumerate(ts)if t.start==scope.start),None)
                if at is None:continue
                ms=matching(ts);j=at+1
                if ts[at].value=='function'and j<len(ts)and ts[j].kind=='id':j+=1
                if j<len(ts)and ts[j].value=='('and ms[j]<len(ts):
                    for arg in split_tokens(ts[j+1:ms[j]]):
                        if arg and arg[0].kind=='id'and arg[0].value==name:return False
            scope=scope.parent
        return True
    def p4_flow_publication_object(self,spec,model,parent,keymap,aliases,selected_owner):
        if not isinstance(spec,dict)or not IDENT.fullmatch(spec.get('symbol','')):raise Unsupported('Missing explicit const publication object binding')
        e=self.evidence(spec.get('evidence'));name=spec['symbol'];found=[]
        if e.model is not model:raise Unsupported('Publication object definition moved to another source')
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts[:-4]):
                if [x.value for x in ts[i:i+4]]!=['const',name,'=','{']or model.scope(t.start)is not parent:continue
                close=ms[i+3]
                if e.start<=t.start and ts[close].end<=e.end:found.append((ts[i+1],ts[i+3:close+1]))
        if len(found)!=1:raise Unsupported('Publication object lacks one exact original const literal')
        declaration,obj=found[0];self.p2_no_owner_writes(model,[(name,)],[declaration]);allowed=[declaration];targets=[];lhs=[]
        for pub in spec.get('publications',[]):
            pe=self.evidence(pub.get('evidence'));target=normalized_callee(pub.get('target'));hits=[]
            if pe.model is not model or not target or target in targets:raise Unsupported('Const-object publication target is disconnected or duplicated')
            for ts in model.lists:
                for i,t in enumerate(ts):
                    if t.kind!='id'or i and ts[i-1].value in {'.','?.'}:continue
                    path=self.p4_flow_path(ts,i,keymap,aliases)
                    if not path or path[0]!=target:continue
                    j=path[1]
                    if j+2<len(ts)and [x.value for x in ts[j:j+3]]==['=',name,';']and pe.start<=t.start and ts[j+2].end<=pe.end and model.scope(t.start)is parent:hits.append((t,ts[j+1]))
            if len(hits)!=1:raise Unsupported('Const object is not directly published at its exact static target')
            lhs.append(hits[0][0]);allowed.append(hits[0][1]);targets.append(target)
        if selected_owner not in targets:raise Unsupported('Const-object evidence does not publish the selected owner')
        for ts in model.lists:
            for i,t in enumerate(ts):
                if t.kind!='id'or t.value!=name or not self.p4_flow_same_binding(model,name,t.start,parent)or any(t is a for a in allowed)or i and ts[i-1].value in {'.','?.'}:continue
                raise Unsupported('Const publication object has an unproven extra member/copy/forward use')
        return dict(symbol=name,tokens=obj,evidence=e,targets=targets,lhs=lhs,allowed=allowed)

    def p4_flow_selected_key_stable(self,model,fn,key,selected,route=None,allowed_resolved_after=None,context_spec=None):
        sources=[]
        if context_spec is not None:
            verifier=getattr(self,'p4_flow_context_false_ranges',None)
            if not callable(verifier):raise Unsupported('Finite source-context route exclusion is not implemented')
            ranges=verifier(context_spec,model,fn)
            if not isinstance(ranges,list)or any(not isinstance(r,tuple)or len(r)!=2 or not all(type(x)is int for x in r)or not fn.body_start<=r[0]<r[1]<=fn.body_end for r in ranges):raise Unsupported('Source-context exclusion lacks exact validated original branch ranges')
            sources.extend(ranges)
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts):
                if not(fn.body_start<t.start<fn.body_end) or model.enclosing_function(t.start)is not fn:continue
                if t.value not in {'if','while'} or i+1>=len(ts) or ts[i+1].value!='(':continue
                close=ms[i+1];j=close+1
                end=ms[j] if j<len(ts) and ts[j].value=='{' else next((k for k in range(j,len(ts))if ts[k].value==';'),len(ts)-1)
                if self.p1_known_false_key_guard(ts[i+2:close],key,selected,route):sources.append((ts[j].start,ts[end].end))
        for ts in model.lists:
            for i,t in enumerate(ts[:-1]):
                if t.value!=key or not(fn.body_start<t.start<fn.body_end) or model.enclosing_function(t.start)is not fn or i and ts[i-1].value in {'.','?.'}:continue
                if ts[i+1].value not in {'=','+=','-=','++','--'}:continue
                if any(a<=t.start<=b for a,b in sources):continue
                if allowed_resolved_after is not None and t.start>allowed_resolved_after and [x.value for x in ts[i+1:i+6]]==['=','resolved','.','key',';']:continue
                raise Unsupported('Selected route key is rewritten before its proven lookup/dispatch')

    def p4_top_level_leaf_projection(self,model,builder,array_start,selected_start,route_map):
        """Only the original invoked literal-menu top-level leaf projection.

        False leaves existing child handling unchanged. True means the selected
        literal is a direct childless top entry, projected by the whole bounded
        original callback with no conflicting key or unknown map writer.
        """
        if not IDENT.fullmatch(route_map or''):raise Unsupported('Top-level leaf route map must be an original identifier')
        arrays=[]
        for ts in model.lists:
            at=next((i for i,t in enumerate(ts)if t.start==array_start and t.value=='['),None)
            if at is not None:arrays.append((ts,at,matching(ts)[at]))
        if len(arrays)!=1:raise Unsupported('Top-level leaf needs its exact unique original literal menu array')
        ts,at,end=arrays[0]
        if at<3 or[x.value for x in ts[at-3:at-1]]!=['const',ts[at-2].value]or ts[at-1].value!='='or ts[at-2].kind!='id':raise Unsupported('Top-level leaf menu is not its original const literal-array binding')
        collection=ts[at-2].value
        if builder.path!=(collection,'forEach'):raise Unsupported('Top-level leaf builder does not consume the actual literal menu binding')
        tops=[];allkeys=[];selected=None
        for item in split_tokens(ts[at+1:end]):
            if not item:continue
            props=self.p1_props(item);key=self.p1_literal(props.get('key',[]))
            if key is None:raise Unsupported('Top-level menu contains an unknown dynamic key')
            allkeys.append(key);tops.append((item[0].start,props,key))
            if item[0].start==selected_start:selected=(props,key)
            children=props.get('children')
            if children is not None:
                if not children or children[0].value!='['or matching(children).get(0)!=len(children)-1:raise Unsupported('Top-level menu children must remain original literal entries')
                for child in split_tokens(children[1:-1]):
                    if not child:continue
                    cp=self.p1_props(child)
                    if self.p1_literal(cp.get('type',[]))=='section':continue
                    ck=self.p1_literal(cp.get('key',[]))
                    if ck is None:raise Unsupported('Top-level leaf has an unknown projected child key')
                    allkeys.append(ck)
        if selected is None:return False
        props,route_key=selected
        if 'children'in props:raise Unsupported('Selected top-level literal is a group, not a childless leaf')
        if len(allkeys)!=len(set(allkeys)):raise Unsupported('Top-level leaf key collides with another menu entry')
        if len(builder.args)!=1:raise Unsupported('Top-level leaf builder needs one original callback')
        arg=builder.args[0]
        if len(arg)<4 or arg[0].kind!='id'or[x.value for x in arg[1:3]]!=['=>','{']or matching(arg).get(2)!=len(arg)-1:raise Unsupported('Top-level leaf builder is not its exact block group callback')
        group=arg[0].value;body=arg[3:-1];names={}
        for i,t in enumerate(body[:-5]):
            if t.value in {'filter','forEach'}and i and body[i-1].value=='.'and body[i+1].value=='('and body[i+2].kind=='id'and body[i+3].value=='=>':
                if t.value in names:raise Unsupported('Top-level leaf has duplicate child projection callbacks')
                names[t.value]=body[i+2].value
        if set(names)!={'filter','forEach'}or group in names.values():raise Unsupported('Top-level leaf needs its original distinct filter/child callback bindings')
        f=names['filter'];c=names['forEach']
        expected="if(!"+group+".children)"+route_map+'['+group+'.key]={...'+group+',page:'+group+'.page||'+group+'.key,breadcrumb:['+group+'.label]};('+group+".children||[]).filter("+f+"=>"+f+".type!=='section').forEach("+c+"=>{"+route_map+'['+c+'.key]={...'+c+',domain:'+c+'.domain||'+group+'.domain,breadcrumb:['+group+'.label,'+c+'.label],parent:'+group+'.key};});'
        def normalized(tokens):return [('string',literal(t))if t.kind=='string'else(t.kind,t.value)for t in tokens]
        if normalized(body)!=normalized(lex(expected)):raise Unsupported('Top-level leaf guard/page fallback/whole child projection differs from the bounded original source')
        parent=model.enclosing_function(builder.start)
        if parent is None or model.scope(ts[at-3].start)is not parent:raise Unsupported('Top-level leaf builder lacks its original lexical owner')
        self.p1_collection_stable(model,collection,array_start,parent)
        bodies=[]
        for ts in model.lists:
            j=next((i for i,t in enumerate(ts)if t.start==parent.body_start and t.value=='{'),None)
            if j is not None:bodies.append(ts[j+1:matching(ts)[j]])
        if len(bodies)!=1 or not self.p2_direct_statement(bodies[0],builder.start):raise Unsupported('Top-level leaf projection is not an actual directly invoked source statement')
        declarations=[]
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts[:-1]):
                if t.value!=route_map or i and ts[i-1].value in {'.','?.'}:continue
                if ts[i+1].value=='=':
                    if i and ts[i-1].value=='const'and[x.value for x in ts[i+1:i+5]]==['=','{','}',';']and model.scope(t.start)is parent:declarations.append(t);continue
                    raise Unsupported('Top-level leaf route map is replaced')
                if ts[i+1].value not in {'[','.'}:continue
                if ts[i+1].value=='[':
                    close=ms[i+1];key=self.p1_literal(ts[i+2:close]);j=close+1
                else:key=ts[i+2].value;j=i+3
                if builder.start<t.start<builder.end:continue # whole callback was matched above
                while j<len(ts)and ts[j].value in {'.','['}:j=ms[j]+1 if ts[j].value=='['else j+2
                original=j;ops=''
                while j<len(ts)and ts[j].kind=='punct'and ts[j].value not in {'(',')','[',']','{','}',',',';',':','?'}:ops+=ts[j].value;j+=1
                mutation='='in ops and ops not in {'===','!==','==','!=','<=','>='}or ops in {'++','--'}or i and ts[i-1].value in {'delete','++','--'}
                if mutation and(key is None or key==route_key):raise Unsupported('Top-level selected route has an unknown or conflicting later map write')
        if len(declarations)!=1:raise Unsupported('Top-level leaf needs one original immutable route-map initializer')
        for call in model.calls:
            if call.path in {('Object','assign'),('Object','defineProperty'),('Object','defineProperties'),('Object','setPrototypeOf'),('Reflect','set'),('Reflect','deleteProperty')}and call.args:
                first=path_at(call.args[0],0,False,partial=True)
                if first and first[0][:1]==(route_map,):raise Unsupported('Top-level route map is mutated through an unproved helper')
        self.no_shadow(model,route_map,parent,[builder.start])
        self.p4_top_leaf_no_escapes(model,collection,route_map,declarations+[arrays[0][0][arrays[0][1]-2]],route_key)
        return True

    def p4_top_leaf_no_escapes(self,model,collection,route_map,bindings,route_key):
        allowed=list(bindings);aliases=[]
        # Actual static window publications are source-bound aliases. Local
        # copies, parenthesized forwarding and unknown whole-value callbacks
        # are not inferred. No source function or JavaScript is evaluated.
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts):
                if t.value!='window'or i and ts[i-1].value in {'.','?.'}:continue
                p=path_at(ts,i,False)
                if not p or len(p[0])<2 or p[1]+1>=len(ts)or ts[p[1]].value!='=':continue
                value=p[1]+1
                if ts[value].value in {collection,route_map}and value+1<len(ts)and ts[value+1].value==';':
                    allowed.append(ts[value]);aliases.append((p[0],ts[value].value));continue
                if ts[value].value!='{':continue
                end=ms[value]
                for part in split_tokens(ts[value+1:end]):
                    if len(part)==1 and part[0].value in {collection,route_map}:allowed.append(part[0]);aliases.append((p[0]+(part[0].value,),part[0].value))
                    elif len(part)==3 and part[1].value==':'and part[2].value in {collection,route_map}:
                        key=part[0].value if part[0].kind=='id'else literal(part[0])
                        if key is None:raise Unsupported('Top-level route publication has an unknown map property')
                        allowed.append(part[2]);aliases.append((p[0]+(key,),part[2].value))
        for call in model.calls:
            if call.path==('Object','keys')and len(call.args)==1 and len(call.args[0])==1 and call.args[0][0].value in {collection,route_map}:
                allowed.append(call.args[0][0]);self.no_shadow(model,'Object',None,[call.start],include_parent=True)
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts):
                if t.value not in {collection,route_map}or i and ts[i-1].value in {'.','?.'}or any(t is a for a in allowed):continue
                p=path_at(ts,i,False,partial=True)
                if p and p[0]==(t.value,)and not(p[1]<len(ts)and ts[p[1]].value in {'.','['}):raise Unsupported('Top-level route/menu has an unproved whole-value copy or forwarding escape')
            for i,t in enumerate(ts):
                if t.kind!='id'or i and ts[i-1].value in {'.','?.'}:continue
                p=path_at(ts,i,False,partial=True)
                if not p:continue
                for alias,origin in aliases:
                    if p[0][:len(alias)]!=alias:continue
                    j=p[1];key=p[0][len(alias)]if len(p[0])>len(alias)else None
                    if j<len(ts)and ts[j].value=='[':j=ms[j]+1
                    while j<len(ts)and ts[j].value in {'.','['}:j=ms[j]+1 if ts[j].value=='['else j+2
                    ops='';at=j
                    while at<len(ts)and ts[at].kind=='punct'and ts[at].value not in {'(',')','[',']','{','}',',',';',':','?'}:ops+=ts[at].value;at+=1
                    mutation='='in ops and ops not in {'===','!==','==','!=','<=','>='}or ops in {'++','--'}or i and ts[i-1].value in {'delete','++','--'}
                    # The publication itself assigns the alias owner and does
                    # not mutate the referenced literal map/menu.
                    publication=len(p[0])==len(alias)and j<len(ts)and ts[j].value=='='and j+1<len(ts)and ts[j+1].value==origin
                    if mutation and not publication and(origin==collection or key is None or key==route_key):raise Unsupported('Top-level published map/menu alias has an unknown or selected-slot write')
        for call in model.calls:
            if call.path in {('Object','assign'),('Object','defineProperty'),('Object','defineProperties'),('Object','setPrototypeOf'),('Reflect','set'),('Reflect','deleteProperty')}and call.args:
                p=path_at(call.args[0],0,False,partial=True)
                if p and any(p[0][:len(a)]==a for a,n in aliases):raise Unsupported('Top-level published map/menu alias is mutated by a helper')

    def p4r_function(self,spec,parent=None):
        if not isinstance(spec,dict)or not IDENT.fullmatch(spec.get('symbol','')):raise Unsupported('Resource source function needs its exact static symbol')
        e=self.evidence(spec.get('evidence'));fn,params,body=self.p4_flow_function(e,spec['symbol'],parent)
        return dict(evidence=e,function=fn,parent=fn.parent,params=params,body=body,model=e.model,symbol=spec['symbol'])

    def p4r_return(self,info):
        model,fn,body=info['model'],info['function'],info['body'];hits=[];ms=matching(body)
        for i,t in enumerate(body):
            if t.value!='return'or model.enclosing_function(t.start)is not fn:continue
            at=i+1
            while at<len(body)and body[at].value!=';':at=ms[at]+1 if body[at].value in {'(','[','{'}else at+1
            if self.p2_direct_statement(body,t.start):hits.append(body[i+1:at])
        if len(hits)!=1:raise Unsupported('Resource HTML producer needs one direct original result expression')
        return hits[0]

    def p4r_const(self,info,name,before):
        model,fn=info['model'],info['function'];hits=[]
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts[:-3]):
                if t.value!='const'or i and ts[i-1].value=='('or model.enclosing_function(t.start)is not fn or t.start>=before:continue
                at=i+1
                while at<len(ts)and ts[at].value!=';':at=ms[at]+1 if ts[at].value in {'(','[','{'}else at+1
                for part in split_tokens(ts[i+1:at]):
                    if len(part)>2 and [x.value for x in part[:2]]==[name,'=']:hits.append((part[0],part[2:]))
        if len(hits)!=1:raise Unsupported('Resource HTML/columns value lacks one actual local const definition')
        return hits[0]

    def p4r_literal_templates(self,info,expr,seen=None):
        # Finite HTML result only: a returned template or a plus-concatenation
        # of local const HTML values/templates. No invocation/evaluation of JS.
        seen=set()if seen is None else seen;ms=matching(expr);parts=[];start=0;at=0
        while at<len(expr):
            if expr[at].value in {'(','[','{'}:at=ms[at]+1;continue
            if expr[at].value=='+':parts.append(expr[start:at]);start=at+1
            at+=1
        parts.append(expr[start:]);result=[]
        for part in parts:
            if len(part)==1 and part[0].kind=='template':
                html,_=template_markup(part[0]);balanced=P3BalancedHTML();balanced.feed(html);balanced.close()
                if not balanced.valid or balanced.stack:raise Unsupported('Resource concatenated HTML fragment changes sibling context')
                result.append(part[0]);continue
            if len(part)==1 and part[0].kind=='string'and literal(part[0])=='':continue
            if len(part)==1 and part[0].kind=='id':
                name=part[0].value
                if name in seen:raise Unsupported('Resource HTML local alias cycle')
                token,value=self.p4r_const(info,name,part[0].start)
                self.p4_no_write_contexts(info['model'],{name},token.start-1,info['function'].body_end,[token])
                result+=self.p4r_literal_templates(info,value,seen|{name});continue
            raise Unsupported('Resource returned HTML has an unsupported expression/producer')
        return result

    def p4r_returned_delegate(self,current,step):
        if not isinstance(step,dict)or step.get('kind')!='returned_named_delegate':raise Unsupported('Resource render edge is not a supported actual returned delegate')
        target=self.p4r_function(step.get('target'),current['function'].parent)
        if target['model']is not current['model']:raise Unsupported('Resource direct render delegate moved to another source')
        expr=self.p4r_return(current);cs=calls(expr)
        if len(cs)!=1 or cs[0].index!=0 or cs[0].end_index!=len(expr)-1 or cs[0].path!=(target['symbol'],)or any(cs[0].args):raise Unsupported('Resource render does not return the exact named zero-argument delegate')
        self.no_shadow(current['model'],target['symbol'],current['function'].parent,[cs[0].start])
        return target

    def p4r_table(self,info,content,alias):
        if not isinstance(content,dict)or content.get('version')!='bounded-resource-table@1':raise Unsupported('Resource table evidence version is unsupported')
        templates=self.p4r_literal_templates(info,self.p4r_return(info));hits=[]
        for template in templates:
            for kind,part in template.parts:
                if kind!='expression'or not self.p1_active_part(template,part):continue
                cs=[c for c in calls(part)if c.index==0 and c.end_index==len(part)-1 and c.path==(alias,'table')]
                if len(cs)==1:hits.append(cs[0])
        if content.get('wrapper'):
            if hits:raise Unsupported('Resource wrapper profile cannot borrow another direct table')
            tc,columns=self.p4r_wrapper_table(info,content,alias,templates)
        else:
            if len(hits)!=1 or len(hits[0].args)<2:raise Unsupported('Resource actual returned HTML lacks one active real table')
            tc=hits[0];columns=tc.args[0]
        name=content.get('columns_symbol')
        if name is not None:
            if not IDENT.fullmatch(name)or [x.value for x in columns]!=[name]:raise Unsupported('Resource table does not consume its declared original columns const')
            token,columns=self.p4r_const(info,name,tc.start)
            self.p4_no_write_contexts(info['model'],{name},token.start-1,tc.end,[token])
            self.p2_protected_escapes(info['model'],{name},token.start-1,tc.end,[token,tc.args[0][0]],label='Resource exact columns')
        if not columns or columns[0].value!='['or matching(columns).get(0)!=len(columns)-1:raise Unsupported('Resource columns are not the exact supported literal array')
        entries=split_tokens(columns[1:-1]);slot=content.get('column_source_index');key=content.get('column_key_literal')
        if type(slot)is not int or not 0<=slot<len(entries)or not isinstance(key,str):raise Unsupported('Resource actual column source slot/key missing')
        # Conditional spread support is added only with its own source edge;
        # no silent flattening or permission-dependent index guessing.
        for part in entries:
            if not part or part[0].value!='...':continue
            spread=self.p3_unwrap(part[1:]);sm=matching(spread);q=next((i for i,t in enumerate(spread)if t.value=='?'),None)
            if q is None:raise Unsupported('Resource conditional column spread lacks a finite literal-array branch')
            at=q+1
            if at>=len(spread)or spread[at].value!='[':raise Unsupported('Resource conditional column spread true branch is not an original literal array')
            end=sm[at]
            if [x.value for x in spread[end+1:]]!=[':','[',']']:raise Unsupported('Resource conditional column spread fallback is not the exact empty array')
            for column in split_tokens(spread[at+1:end]):
                cp=self.p1_props(column)
                if not isinstance(self.p1_literal(cp.get('key',[])),str):raise Unsupported('Resource conditional spread contains an unknown column producer')
            if entries[slot]is part:raise Unsupported('Resource selected column cannot be guessed inside a conditional spread')
        props=self.p1_props(entries[slot])
        if self.p1_literal(props.get('key',[]))!=key:raise Unsupported('Resource exact original column key/index differs')
        value=props.get('render',[]);vm=matching(value)
        if not value or value[0].value!='(':raise Unsupported('Resource first table profile requires original parenthesized renderer parameters')
        close=vm[0];parts=split_tokens(value[1:close]);params=[p[0].value for p in parts if len(p)==1 and p[0].kind=='id']
        if len(params)!=len(parts)or [x.value for x in value[close+1:close+2]]!=['=>']or len(value)!=close+3 or value[-1].kind!='template':raise Unsupported('Resource renderer is not its actual direct returned template')
        rt=value[-1];self.p4_renderer_readonly(info['model'],rt,params)
        self.p4r_table_rows(info,tc,alias,content.get('pagination'))
        te=self.evidence(content.get('table_implementation'));tx=self.evidence(content.get('table_export'))
        if te.model is not tx.model:raise Unsupported('Resource real table implementation/export disconnected')
        self.p1_table_contract(te,tx,key)
        return dict(template=rt,params=params,key=key,call=tc)

    def p4r_table_rows(self,info,call,alias,pagination):
        model,fn=info['model'],info['function'];arg=call.args[1]
        try:return self.p2_table_rows(model,fn,call,alias)
        except Unsupported:pass
        if not isinstance(pagination,dict):raise Unsupported('Resource original pagination constant needs explicit source evidence')
        e=self.evidence(pagination.get('evidence'));size=pagination.get('page_size_symbol');count=pagination.get('page_size_literal')
        if e.model is not model or not IDENT.fullmatch(size or '')or type(count)is not int or count<1:raise Unsupported('Resource pagination size is not an exact positive original integer')
        bindings=[]
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts):
                if t.value!='const'or i and ts[i-1].value=='('or model.scope(t.start)is not fn.parent:continue
                at=i+1
                while at<len(ts)and ts[at].value!=';':at=ms[at]+1 if ts[at].value in {'(','[','{'}else at+1
                for p in split_tokens(ts[i+1:at]):
                    if len(p)==3 and [x.value for x in p[:2]]==[size,'=']and p[2].kind=='number'and p[2].value==str(count)and e.start<=t.start and p[-1].end<=e.end:bindings.append(p[0])
        if len(bindings)!=1:raise Unsupported('Resource pagination size lacks one exact const declaration')
        self.p2_no_owner_writes(model,[(size,)],bindings)
        cs=[c for c in calls(arg)if c.index==0 and c.end_index==len(arg)-1]
        if len(cs)!=1 or len(cs[0].path)!=2 or cs[0].path[1]!='slice'or len(cs[0].args)!=2:raise Unsupported('Resource original row pagination is not one exact slice')
        rows=cs[0].path[0];start,end=cs[0].args
        if len(start)!=1 or start[0].kind!='id'or [x.value for x in end]!=[start[0].value,'+',size]:raise Unsupported('Resource row slice has different/empty original boundaries')
        token,offset=self.p4r_const(info,start[0].value,call.start);v=[x.value for x in offset];om=matching(offset)
        if not offset or offset[0].value!='('or om[0]+2!=len(offset)-1 or v[-2:]!=['*',size]or v[om[0]-2:om[0]]!=['-','1']:raise Unsupported('Resource row offset is outside its finite original page-minus-one pattern')
        page=offset[1:om[0]-2];path=path_at(page,0,False)
        if not path or path[1]!=len(page):raise Unsupported('Resource original pagination page value is not a static path')
        # Preserve the old source row checks; this branch only supplies the
        # multi-const positive-size declaration omitted by old P1 syntax.
        declarations=[]
        try:const_row_token,_=self.p4r_const(info,rows,call.start)
        except Unsupported:const_row_token=None
        for ts in model.lists:
            for i,t in enumerate(ts[:-2]):
                if t.value!=rows or not(fn.body_start<t.start<call.start)or i and ts[i-1].value in {'.','?.'}:continue
                if ts[i+1].value=='=':
                    if t is const_row_token or i and ts[i-1].value in {'const','let'}:
                        if [x.value for x in ts[i+2:i+4]]==['[',']']:raise Unsupported('Resource row source is explicitly empty')
                        declarations.append(t);continue
                    j=i+2
                    if [x.value for x in ts[j:j+6]]!=[alias,'.','sortByUpdated','?.','(',rows]:raise Unsupported('Resource rows are overwritten before the actual table')
                    close=matching(ts)[j+4]
                    if [x.value for x in ts[close+1:close+4]]!=['||',rows,';']:raise Unsupported('Resource row sorting drops the original fallback')
        if not declarations:
            try:token,value=self.p4r_const(info,rows,call.start)
            except Unsupported:raise Unsupported('Resource table needs its unique original local row source')
            if [x.value for x in value]==['[',']']:raise Unsupported('Resource row source is explicitly empty')
            declarations.append(token)
        if len(declarations)!=1:raise Unsupported('Resource table needs its unique original local row source')
        if any(fn.body_start<c.start<call.start and c.path[:1]==(rows,)and c.path[1:]!=('slice',)for c in model.calls):raise Unsupported('Resource rows are destructively preprocessed')

    def p4r_row_html_helper(self,info,renderer,step):
        if not isinstance(step,dict)or step.get('kind')!='returned_row_html_helper':raise Unsupported('Resource helper output needs its explicit returned HTML edge')
        index=step.get('expression_index');parts=[p for k,p in renderer['template'].parts if k=='expression']
        if type(index)is not int or not 0<=index<len(parts)or not self.p1_active_part(renderer['template'],parts[index]):raise Unsupported('Resource row helper is not inserted into active original HTML')
        expr=parts[index];cs=calls(expr);target=self.p4r_function(step.get('target'),info['function'].parent)
        if target['model']is not info['model']or len(cs)!=1 or cs[0].index!=0 or cs[0].end_index!=len(expr)-1 or cs[0].path!=(target['symbol'],)or len(cs[0].args)!=len(target['params']):raise Unsupported('Resource row helper call/return is not the exact source function')
        row_index=step.get('row_argument_index')
        if type(row_index)is not int or not 0<=row_index<len(target['params'])or len(renderer['params'])<2 or [x.value for x in cs[0].args[row_index]]!=[renderer['params'][1]]:raise Unsupported('Resource row helper does not receive the exact actual table row')
        row=target['params'][row_index]
        self.p4_no_write_contexts(target['model'],set(target['params']),target['function'].body_start,target['function'].body_end)
        self.p2_protected_escapes(target['model'],{row},target['function'].body_start,target['function'].body_end,label='Resource row helper identity')
        self.p2_protected_escapes(info['model'],{renderer['params'][1]},renderer['template'].start,renderer['template'].end,[cs[0].args[row_index][0]],label='Resource proven row-helper forwarding')
        self.no_shadow(info['model'],target['symbol'],info['function'].parent,[cs[0].start])
        return target,row,self.p4r_literal_templates(target,self.p4r_return(target))

    def p4_resource_flow(self,flow):
        entry=flow.get('entry_binding');event=flow.get('event_binding');result=flow.get('result_binding')
        if not isinstance(entry,dict)or entry.get('version')!='bounded-resource-entry@1'or not isinstance(event,dict)or event.get('version')!='bounded-resource-event@1'or not isinstance(result,dict):raise Unsupported('Resource active flow lacks its explicit entry/event/result contract')
        if entry.get('kind')=='query_result':return self.p4r_query_flow(flow)
        if entry.get('kind')=='form_mode':return self.p4r_mode_flow(flow)
        if entry.get('kind')=='provider_account':return self.p4r_provider_flow(flow)
        if entry.get('kind')=='skill_form':return self.p4r_skill_form(flow)
        if entry.get('kind')=='agent_form':return self.p4r_agent_form(flow)
        if entry.get('kind')=='component_import':return self.p4r_component_import(flow)
        if entry.get('kind')=='account_bind':return self.p4r_account_bind(flow)
        if entry.get('kind')not in {'table_row_helper','table_scalar_helper','table_direct'}or result.get('kind')!='complete_handler':raise Unsupported('This resource active-flow substructure has not yet been implemented')
        route=self.p4_flow_route(entry.get('route'));pub=self.p4_flow_export(entry.get('page_export'));current=self.p4r_function(entry.get('render'),pub['parent']);self.p4r_page_route(pub,route)
        if current['function']is not pub['function']or current['model']is not pub['model']:raise Unsupported('Resource route render is not the actual published implementation')
        alias=entry.get('ui_alias');self.p4_flow_ui_alias(entry.get('ui_alias_evidence'),alias,pub['parent'],current['function'].start)
        steps=flow['flow_steps'];at=0
        current['route_view']=route['route_view']
        while at<len(steps)and steps[at].get('kind')in {'returned_named_delegate','route_view_delegate'}:
            current=self.p4r_view_delegate(current,steps[at])if steps[at]['kind']=='route_view_delegate'else self.p4r_returned_delegate(current,steps[at]);at+=1
        renderer=self.p4r_table(current,entry.get('content'),alias)
        scalar_identity=None
        if entry['kind']=='table_scalar_helper':
            if at+1!=len(steps):raise Unsupported('Resource scalar HTML helper has unused source edges')
            target,scalar_identity,templates=self.p4r_scalar_html_helper(current,renderer,steps[at]);row=None
        elif entry['kind']=='table_row_helper':
            if at+1!=len(steps):raise Unsupported('Resource source-edge sequence contains unused or unproved steps')
            target,row,templates=self.p4r_row_html_helper(current,renderer,steps[at])
        else:
            if at<len(steps)and steps[at].get('kind')=='table_to_original_handler':at+=1
            if at!=len(steps):raise Unsupported('Resource direct table event contains an unused claimed edge')
            target=current;row=renderer['params'][1]if len(renderer['params'])>1 else None;templates=[renderer['template']]
            if 'nested_event_path'in event:
                templates=[self.p3_nested_event_template(event['nested_event_path'],self.evidence(event['evidence']),renderer['template'],renderer['params'])]
        callee=normalized_callee(event.get('callee'))
        actual=self.proof['source_chain']['entry'];ee=self.evidence(event.get('evidence'));ae=self.evidence(actual.get('evidence'))
        if event.get('event')!=actual.get('event')or callee!=normalized_callee(actual.get('callee'))or ee.model is not target['model']or(ee.start,ee.end)!=(ae.start,ae.end):raise Unsupported('Resource actual event moved from its exact original control/source')
        helper=self.p4r_escaped_helper(event.get('handler_helper'),pub['parent'],ee.start)if 'handler_helper'in event else None
        if helper and(helper['model']is not ee.model or helper['owner']+(helper['method'],)!=callee):raise Unsupported('Resource row event helper owner/method differs')
        if helper:
            hp=event['handler_helper'];escape=self.evidence(hp.get('escape_implementation_evidence'));export=self.evidence(hp.get('escape_export_evidence'))
            if escape.model is not export.model:raise Unsupported('Resource event escape provenance disconnected')
            self.p2_escape(escape,export)
        index=event.get('identity_argument_index');field=event.get('identity_field')
        if type(index)is not int or index<0 or not IDENT.fullmatch(field or ''):raise Unsupported('Resource row identity field/slot is not explicit')
        distinct=field!=renderer['key']
        if distinct and(entry['kind']!='table_direct'or event.get('identity_kind')!='row_field_distinct_display'or entry.get('content',{}).get('row_identity_field')!=field):raise Unsupported('Resource row identity differs from the selected original display column without its explicit source-field contract')
        found=[]
        for template in templates:
            if not ee.contains(template):continue
            html,parts=template_markup(template);parser=TableHTMLContext(parts);parser.feed(html);parser.close()
            for kind,handler in parser.handlers:
                if kind!=event['event']or not exact_handler(handler,callee,parts,helper):continue
                if helper:
                    args=calls(parts.get(handler.strip(),[]));arguments=args[0].args[1:]if len(args)==1 else []
                else:
                    try:args=calls(lex(handler.strip().removesuffix(';').strip()))
                    except Unsupported:continue
                    arguments=[]
                    if len(args)==1:
                        for arg in args[0].args:
                            marker=(literal(arg[0])if arg[0].kind=='string'else arg[0].value)if len(arg)==1 else None
                            arguments.append(parts.get(marker,arg))
                origin=self.p4_identity_origin(arguments[index],renderer['params'],field)if len(arguments)>index else None
                scalar_good=False
                if entry['kind']=='table_scalar_helper'and len(arguments)>index:
                    identity=event.get('identity_escape',{});escape_alias=identity.get('alias');ice=self.evidence(identity.get('alias_evidence'));self.p4r_escape_alias(ice,escape_alias,pub['parent'],ee.start,[]);self.p2_escape(self.evidence(identity.get('implementation_evidence')),self.evidence(identity.get('export_evidence')))
                    parsed=[c for c in calls(arguments[index])if c.index==0 and c.end_index==len(arguments[index])-1]
                    scalar_good=len(parsed)==1 and parsed[0].path==(escape_alias,)and len(parsed[0].args)==1 and[x.value for x in parsed[0].args[0]]==[scalar_identity]
                good=scalar_good or(entry['kind']=='table_row_helper'and len(arguments)>index and [x.value for x in arguments[index]]==[row,'.',field])or(entry['kind']=='table_direct'and origin is not None and(not distinct or origin=='row'))
                if good:
                    for literal_condition in event.get('literal_arguments',[]):
                        ix=literal_condition.get('argument_index');value=literal_condition.get('literal')
                        if type(ix)is not int or not 0<=ix<len(arguments)or len(arguments[ix])!=1 or self.p3_literal(arguments[ix])!=self.p3_claim_literal(value):raise Unsupported('Resource event mode/context literal is not its exact original argument')
                    if entry['kind']=='table_direct'and origin=='row':self.p2_protected_escapes(current['model'],{row},renderer['template'].start,renderer['template'].end,label='Resource direct actual row identity')
                    conditions=event.get('value_conditions',[])
                    if not isinstance(conditions,list):raise Unsupported('Resource conditional event values must be finite structured evidence')
                    for condition in conditions:
                        ix=condition.get('argument_index');source=condition.get('source_expression',{});value=condition.get('selected_value')
                        if type(ix)is not int or ix<0 or len(arguments)<=ix or type(value)is not bool or source.get('operator')!='==='or type(source.get('literal'))is not bool or not IDENT.fullmatch(source.get('row_field','')):raise Unsupported('Resource toggle boolean source/selection is outside its finite original row comparison')
                        expected=[row,'.',source['row_field'],'===','true'if source['literal']else'false']
                        if [x.value for x in arguments[ix]]!=expected:raise Unsupported('Resource requested state is not derived from the actual original row comparison')
                    found.append(template)
        if len(found)!=1:raise Unsupported('Resource active row helper has no unique exact actual identity event')
        out=self.p4r_function(result)
        binding=self.p4_flow_export(result.get('owner_binding'))
        if out['function']is not binding['function']or out['model']is not binding['model']or binding['callee']!=callee:raise Unsupported('Resource complete handler is not the actual selected event owner')
        terminal=self.proof['source_chain']['terminal'];te=self.evidence(terminal.get('evidence'))
        if terminal.get('symbol')!=out['symbol']or te.model is not out['model']or not te.contains(out['function']):raise Unsupported('Resource result replaces the original candidate implementation')
        return dict(entry_evidence=ee,terminal_evidence=te,terminal_symbol=out['symbol'],result_evidence=out['evidence'],result_symbol=out['symbol'],followup_verified=False)

    def p4r_active_helper(self,info,step):
        if not isinstance(step,dict)or step.get('kind')!='returned_html_helper':raise Unsupported('Resource HTML helper needs one explicit returned active source edge')
        target=self.p4r_function(step.get('target'),info['function'].parent)
        if target['model']is not info['model']:raise Unsupported('Resource returned HTML helper source differs')
        templates=self.p4r_literal_templates(info,self.p4r_return(info));found=[]
        for template in templates:
            for k,part in template.parts:
                if k!='expression'or not self.p1_active_part(template,part):continue
                cs=[c for c in calls(part)if c.index==0 and c.end_index==len(part)-1 and c.path==(target['symbol'],)]
                if len(cs)==1:found.append(cs[0])
        if len(found)!=1 or len([arg for arg in found[0].args if arg])!=len(target['params']):raise Unsupported('Resource returned helper is unused, not directly active, or has different arguments')
        call=found[0];self.no_shadow(info['model'],target['symbol'],info['function'].parent,[call.start]);self.p4_no_write_contexts(target['model'],set(target['params']),target['function'].body_start,target['function'].body_end)
        target['argument_bindings']={name:arg for name,arg in zip(target['params'],[arg for arg in call.args if arg])}
        return target

    def p4r_html_event_arguments(self,templates,event,parent):
        callee=normalized_callee(event.get('callee'));ee=self.evidence(event.get('evidence'));helper=None
        if event.get('handler_helper'):
            helper=self.p4r_escaped_helper(event['handler_helper'],parent,ee.start)
            if helper['owner']+(helper['method'],)!=callee:raise Unsupported('Resource actual HTML helper has a different fixed event owner')
            hp=event['handler_helper'];self.p2_escape(self.evidence(hp.get('escape_implementation_evidence')),self.evidence(hp.get('escape_export_evidence')))
        hits=[]
        for template in templates:
            if not ee.contains(template):continue
            html,parts=template_markup(template)if template.kind=='template'else self.p4_rep_markup([template]);parser=TableHTMLContext(parts);parser.feed(html);parser.close()
            for kind,handler in parser.handlers:
                if kind!=event.get('event'):continue
                if helper:
                    ts=parts.get(handler.strip(),[]);cs=[c for c in calls(ts)if c.index==0 and c.end_index==len(ts)-1]
                    if len(cs)!=1 or cs[0].path!=(helper['name'],)or not cs[0].args or self.p1_literal(cs[0].args[0])!=helper['method']:continue
                    args=cs[0].args[1:]
                else:
                    if not exact_handler(handler,callee,parts,None):continue
                    try:cs=calls(lex(handler.strip().removesuffix(';').strip()))
                    except Unsupported:continue
                    if len(cs)!=1:continue
                    args=[]
                    for arg in cs[0].args:
                        marker=(literal(arg[0])if arg[0].kind=='string'else arg[0].value)if len(arg)==1 else None
                        args.append(parts.get(marker,arg))
                good=True
                for condition in event.get('literal_arguments',[]):
                    ix=condition.get('argument_index')
                    if type(ix)is not int or ix<0 or ix>=len(args):raise Unsupported('Resource actual literal event argument index differs')
                    try:value=self.p4r_scalar(args[ix],{})
                    except Unsupported:good=False;break
                    if type(value)is not type(condition.get('literal'))or value!=condition.get('literal'):good=False;break
                if good:hits.append(args)
        if len(hits)!=1:raise Unsupported('Resource HTML has no unique active exact original event')
        return ee,hits[0]

    def p4r_refresh(self,query,spec,route_key,require_dom=True):
        if not isinstance(spec,dict)or spec.get('version')!='bounded-current-query-return@1':raise Unsupported('Resource query needs explicit original route return evidence')
        model,fn=query['model'],query['function'];kind=spec.get('kind');cs=[]
        if kind=='literal_selector_navigate':
            bindings=query.get('literal_bindings',{})
            for c in model.calls:
                if model.enclosing_function(c.start)is not fn or c.path not in {('navigateTo',),('window','navigateTo')}or len(c.args)!=1:continue
                if self.p4r_scalar(c.args[0],bindings)==route_key:self.p4_rep_call_guards(query,c,bindings);cs.append(c)
        elif kind=='keyed_delegate':
            delegate=self.p4r_function(spec.get('handler'),fn.parent);bindings=query.get('literal_bindings',{});calls_=[c for c in model.calls if model.enclosing_function(c.start)is fn and c.path==(delegate['symbol'],)]
            if len(calls_)!=1 or len([a for a in calls_[0].args if a])!=len(delegate['params']):raise Unsupported('Resource query does not call its original keyed route delegate')
            delegate['literal_bindings']={name:self.p4r_scalar(arg,bindings)for name,arg in zip(delegate['params'],[a for a in calls_[0].args if a])}
            self.p4_no_write_contexts(delegate['model'],set(delegate['params']),delegate['function'].body_start,delegate['function'].body_end)
            target=spec.get('route_function')
            if target:
                route_fn=self.p4r_function(target,fn.parent);rc=[c for c in model.calls if model.enclosing_function(c.start)is delegate['function']and c.path==(route_fn['symbol'],)]
                if len(rc)!=1 or len(rc[0].args)!=len(route_fn['params']):raise Unsupported('Resource actual route name helper is missing or has different arguments')
                selected={name:self.p4r_scalar(arg,delegate['literal_bindings'])for name,arg in zip(route_fn['params'],rc[0].args)}
                if self.p4r_scalar(self.p4r_return(route_fn),selected)!=route_key:raise Unsupported('Resource actual literal route helper selects a different route')
                nav=[c for c in model.calls if model.enclosing_function(c.start)is delegate['function']and c.path==('navigateTo',)and len(c.args)==1 and c.args[0]and c.args[0][0].start==rc[0].start and c.args[0][-1].end==rc[0].end]
                if len(nav)!=1:raise Unsupported('Resource route helper result is not consumed by its actual navigation call')
                self.p4_rep_call_guards(delegate,nav[0],delegate['literal_bindings']);self.no_shadow(model,route_fn['symbol'],fn.parent,[rc[0].start]);cs=calls_
            else:
                self.p4r_refresh(delegate,spec.get('route_return'),route_key,False);cs=calls_
            self.no_shadow(model,delegate['symbol'],fn.parent,[calls_[0].start])
        elif kind=='named_refresh':
            refresh=self.p4r_function(spec.get('handler'),fn.parent)
            if refresh['model']is not model or refresh['params']:raise Unsupported('Resource named refresh is not the original zero-argument same-scope handler')
            cs=[c for c in model.calls if model.enclosing_function(c.start)is fn and c.path==(refresh['symbol'],)and not any(c.args)]
            self.p4r_refresh(refresh,spec.get('route_return'),route_key,False)
            self.no_shadow(model,refresh['symbol'],fn.parent,[c.start for c in cs])
        elif kind=='navigate_literal':
            cs=[c for c in model.calls if model.enclosing_function(c.start)is fn and c.path in {('navigateTo',),('window','navigateTo')}and len(c.args)>=1 and self.p1_literal(c.args[0])==route_key and(not c.args[1:]or len(c.args)==2 and[x.value for x in c.args[1]]==['{','...',spec.get('route_options_state','__no_state__'),'}'])]
            for c in cs:self.no_shadow(model,c.path[0],None,[c.start],include_parent=True)
        elif kind=='refresh_current':
            receiver=normalized_callee(spec.get('callee','RouteRuntime.refreshCurrent'))
            cs=[c for c in model.calls if model.enclosing_function(c.start)is fn and c.path==receiver and not any(c.args)]
            e=self.evidence(spec.get('publication_evidence'));nav=self.p4r_function(spec.get('render_current'));body=[t.value for t in nav['body']]
            # Real current-route refresh: preserve existing options and render
            # the same lastNavigation route into the actual current DOM node.
            expected=['const','{','key','}','=','lastNavigation',',','route','=','routes','[','key',']',',','page','=','window','.','Pages','[','route','?.','page',']',',','content','=','document','.','getElementById','(']
            if body[:len(expected)+8].count('lastNavigation')<1 or not any(body[i:i+len(expected)]==expected for i in range(len(body)-len(expected)+1)):raise Unsupported('Resource refresh does not retain the actual current route/page/DOM binding')
            sequence=['const','opts','=','{','...','lastNavigation','.','options',',','...','(','options','||','{','}',')','}',';']
            if not any(body[i:i+len(sequence)]==sequence for i in range(len(body)-len(sequence)+1)):raise Unsupported('Resource refresh replaces the original current options')
            sinks=[c for c in nav['model'].calls if nav['evidence'].contains(c)and c.path==('page','render')and len(c.args)==1 and[x.value for x in c.args[0]]==['opts']and[x.value for x in c.tokens[max(0,c.index-4):c.index]]==['content','.','innerHTML','=']]
            if len(sinks)!=1:raise Unsupported('Resource query refresh lacks its real current DOM render sink')
            declarations=[]
            for i in range(len(nav['body'])-len(expected)+1):
                if[x.value for x in nav['body'][i:i+len(expected)]]==expected:
                    declarations +=[nav['body'][i+expected.index(name)]for name in ['page','content']]
            for i in range(len(nav['body'])-len(sequence)+1):
                if[x.value for x in nav['body'][i:i+len(sequence)]]==sequence:declarations.append(nav['body'][i+1])
            if len(declarations)!=3:raise Unsupported('Resource refresh current page/options initializer is duplicated or replaced')
            self.p4_no_write_contexts(nav['model'],{'page','content','opts'},nav['function'].body_start,sinks[0].end,declarations+[sinks[0].tokens[sinks[0].index-4]])
            actual=[]
            for ts in e.model.lists:
                ms=matching(ts)
                for i,t in enumerate(ts):
                    if t.value!='refreshCurrent'or [x.value for x in ts[i+1:i+5]]!=[':', '(', ')','=>']:continue
                    calls_=[c for c in e.model.calls if c.tokens is ts and c.index==i+5 and c.path==(nav['symbol'],)and len(c.args)==1]
                    if len(calls_)!=1 or[x.value for x in calls_[0].args[0]]!=['{','preserveFilters',':','true','}']:continue
                    if e.contains(calls_[0])and e.model is nav['model']:actual.append(calls_[0])
            if len(actual)!=1:raise Unsupported('Resource refresh publication is not the actual fixed current-route renderer')
            if spec.get('publication_aliases'):
                aliases=[self.p4_flow_root_alias(item,model,fn.parent)for item in spec['publication_aliases']]
                if not any(receiver[0]==name and target==('window',)for name,target in aliases):raise Unsupported('Resource refresh root is not the exact original window IIFE binding')
            else:self.no_shadow(model,receiver[0],None,[c.start for c in cs],include_parent=True)
        else:raise Unsupported('Resource query route return form is not supported')
        if len(cs)!=1:raise Unsupported('Resource query has no unique actual return to its original route')
        if not self.p2_direct_statement(query['body'],cs[0].start):
            guards=self.p4_rep_call_guards(query,cs[0],query.get('literal_bindings',{}))
            if not guards and not self.p4_rep_direct_body_token(model,fn,cs[0].tokens[cs[0].index]):raise Unsupported('Resource query route edge has an unproved structural parent')
            for condition,positive in guards:
                truth=self.p4_rep_condition(condition,query.get('literal_bindings',{}))
                if truth is not None and truth!=positive:raise Unsupported('Resource query route edge is excluded by its original literal guard')
        if require_dom and not any(c.path[:2]==('document','getElementById')and model.enclosing_function(c.start)is fn for c in model.calls):
            reader=self.p4r_function(spec.get('input_reader'),fn.parent);reads=[c for c in model.calls if model.enclosing_function(c.start)is fn and c.path==(reader['symbol'],)and not any(c.args)and c.start<cs[0].start]
            if reader['params']or len(reads)!=1 or not self.p2_direct_statement(query['body'],reads[0].start):raise Unsupported('Resource query does not directly consume its original filter input reader')
            if not any(c.path==('document','querySelector')and reader['model'].enclosing_function(c.start)is reader['function']for c in reader['model'].calls):raise Unsupported('Resource query input reader lacks its actual original DOM source')
            self.no_shadow(model,reader['symbol'],fn.parent,[reads[0].start])
        return cs[0]

    def p4r_query_flow(self,flow):
        entry,event,result=flow['entry_binding'],flow['event_binding'],flow['result_binding'];route=self.p4_flow_route(entry.get('route'));pub=self.p4_flow_export(entry.get('page_export'));current=self.p4r_function(entry.get('render'),pub['parent']);self.p4r_page_route(pub,route)
        if current['function']is not pub['function']:raise Unsupported('Resource query render is not its actual published page')
        alias=entry.get('ui_alias');self.p4_flow_ui_alias(entry.get('ui_alias_evidence'),alias,pub['parent'],current['function'].start)
        steps=flow['flow_steps'];at=0
        current['route_view']=route['route_view']
        while at<len(steps)and steps[at].get('kind')in {'returned_named_delegate','route_view_delegate'}:
            current=self.p4r_view_delegate(current,steps[at])if steps[at]['kind']=='route_view_delegate'else self.p4r_returned_delegate(current,steps[at]);at+=1
        result_info=self.p4r_function(result)
        if current['function']is not result_info['function']:raise Unsupported('Resource query result does not preserve its original observed renderer')
        self.p4r_table(current,entry.get('content'),alias)
        producer=current
        while at<len(steps)and steps[at].get('kind')=='returned_html_helper':producer=self.p4r_active_helper(producer,steps[at]);at+=1
        if at<len(steps)and steps[at].get('kind')=='query_to_current_route_result':at+=1
        if at!=len(steps):raise Unsupported('Resource query contains unconsumed source edges')
        templates=self.p4r_literal_templates(producer,self.p4r_return(producer));ee,args=self.p4r_html_event_arguments(templates,event,pub['parent']);actual=self.proof['source_chain']['entry'];ae=self.evidence(actual.get('evidence'))
        if event.get('event')!=actual.get('event')or normalized_callee(event.get('callee'))!=normalized_callee(actual.get('callee'))or ee.model is not ae.model or(ee.start,ee.end)!=(ae.start,ae.end):raise Unsupported('Resource query control differs from its exact original source entry')
        handler=self.p4r_function(event.get('handler'),pub['parent']);owner=self.p4_flow_export(event.get('owner_binding'))
        if owner['function']is not handler['function']or owner['callee']!=normalized_callee(event.get('callee')):raise Unsupported('Resource query event is not the original published query handler')
        handler['literal_bindings']={}
        producer_bindings=producer.get('argument_bindings',{})
        for name,arg in zip(handler['params'],args):
            source=producer_bindings.get(arg[0].value,arg)if len(arg)==1 and arg[0].kind=='id'else arg
            try:handler['literal_bindings'][name]=self.p4r_scalar(source,{})
            except Unsupported:pass
        self.p4r_refresh(handler,event.get('route_return'),route['route_key'])
        te=self.evidence(self.proof['source_chain']['terminal'].get('evidence'))
        if not te.contains(result_info['function']):raise Unsupported('Resource query terminal moved from its original observed result renderer')
        return dict(entry_evidence=ee,terminal_evidence=te,terminal_symbol=result_info['symbol'],result_evidence=result_info['evidence'],result_symbol=result_info['symbol'],followup_verified=False)

    def p4r_view_delegate(self,current,step):
        target=self.p4r_function(step.get('target'),current['function'].parent);state=step.get('view_variable');option=step.get('view_parameter');selected=current.get('route_view');body=current['body'];ms=matching(body)
        if current['params']!=[option]or not IDENT.fullmatch(state or '')or selected is None or target['model']is not current['model']:raise Unsupported('Resource route-view delegate has different original parameters/view/source')
        assignments=[]
        for i,t in enumerate(body[:-7]):
            if[x.value for x in body[i:i+8]]==[state,'=',option,'?.','view','||',state,';']or[x.value for x in body[i:i+10]]==[state,'=',option,'&&',option,'.','view','||',state,';']:assignments.append(t)
        if len(assignments)!=1:raise Unsupported('Resource route view is not bound from the actual navigation options')
        self.p4_no_write_contexts(current['model'],{state},current['function'].body_start,current['function'].body_end,assignments)
        cs=[c for c in current['model'].calls if current['model'].enclosing_function(c.start)is current['function']and c.path==(target['symbol'],)and not any(c.args)]
        if len(cs)!=1:raise Unsupported('Resource selected view has no unique original returned delegate')
        call=cs[0];found=False
        # The source may use the literal view ternary or a literal-view if
        # branch. Only actual return positions are admitted.
        for i,t in enumerate(body):
            if t.value!='return':continue
            stop=i+1
            while stop<len(body)and body[stop].value!=';':stop=ms[stop]+1 if body[stop].value in {'(','[','{'}else stop+1
            expr=body[i+1:stop]
            if not expr or not(expr[0].start<=call.start and call.end<=expr[-1].end):continue
            outer=[c for c in calls(expr)if c.index==0 and c.end_index==len(expr)-1]
            if len(outer)==1 and outer[0].start==call.start:found=True;break
            ternary=self.p4_rep_unwrap(expr);tm=matching(ternary);q=next((j for j,x in enumerate(ternary)if x.value=='?'),None)
            if q is None:continue
            colon=next((j for j in range(q+1,len(ternary))if ternary[j].value==':'),None)
            if colon is None:continue
            truth=self.p4_rep_condition(ternary[:q],{state:selected})
            branch=ternary[q+1:colon]if truth is True else ternary[colon+1:]if truth is False else []
            if branch and branch[0].start==call.start and branch[-1].end==call.end:found=True;break
        if not found:raise Unsupported('Resource literal selected route view does not actually return its delegate')
        for i,t in enumerate(body[:-1]):
            if t.value!='if'or body[i+1].value!='(':continue
            close=ms[i+1];condition=body[i+2:close];truth=self.p4_rep_condition(condition,{state:selected});start=close+1
            if start>=len(body):continue
            end=ms[start]if body[start].value=='{'else next((j for j in range(start,len(body))if body[j].value==';'),len(body)-1)
            inside=body[start].start<call.start<body[end].end
            if inside and truth is False:raise Unsupported('Resource view delegate is in an impossible selected route branch')
            if not inside and body[end].end<call.start and truth is True and any(x.value=='return'for x in body[start:end+1]):raise Unsupported('Resource selected view is consumed by another earlier returned branch')
        self.no_shadow(current['model'],target['symbol'],current['function'].parent,[call.start]);target['route_view']=selected
        return target

    def p4r_wrapper_table(self,info,content,alias,templates):
        spec=content.get('wrapper');wrapper=self.p4r_function(spec,info['function'].parent);candidates=[]
        for template in templates:
            for kind,part in template.parts:
                if kind=='expression'and self.p1_active_part(template,part):candidates +=[c for c in calls(part)if c.index==0 and c.end_index==len(part)-1 and c.path==(wrapper['symbol'],)]
        if len(candidates)!=1:raise Unsupported('Resource table wrapper is not consumed by the actual returned active HTML')
        caller=candidates[0];ci=spec.get('columns_argument_index');ri=spec.get('rows_argument_index')
        if type(ci)is not int or type(ri)is not int or min(ci,ri)<0 or ci==ri or len(caller.args)!=len(wrapper['params'])or max(ci,ri)>=len(caller.args):raise Unsupported('Resource wrapper original columns/rows parameter slots differ')
        cn,rn=wrapper['params'][ci],wrapper['params'][ri];self.p4_no_write_contexts(wrapper['model'],{cn},wrapper['function'].body_start,wrapper['function'].body_end);
        wt=self.p4r_literal_templates(wrapper,self.p4r_return(wrapper));tables=[]
        for template in wt:
            for kind,part in template.parts:
                if kind=='expression'and self.p1_active_part(template,part):tables +=[c for c in calls(part)if c.index==0 and c.end_index==len(part)-1 and c.path==(alias,'table')]
        if len(tables)!=1 or len(tables[0].args)<2 or[x.value for x in tables[0].args[0]]!=[cn]or not self.p1_slice_rows(tables[0].args[1],rn,wrapper['model'],wrapper['function'].parent):raise Unsupported('Resource wrapper does not return the original forwarded columns/rows in its real table')
        self.p2_protected_escapes(wrapper['model'],{cn},wrapper['function'].body_start,wrapper['function'].body_end,[tables[0].args[0][0]],label='Resource original forwarded columns');self.p1_wrapper_rows_stable(wrapper['model'],wrapper['function'],rn,alias);self.no_shadow(info['model'],wrapper['symbol'],info['function'].parent,[caller.start])
        # Reuse the caller's exact original row producer checks at the
        # verified rows slot, rather than treating wrapper parameters as data.
        surrogate=Call(caller.path,caller.tokens,caller.index,caller.open_index,caller.end_index,[caller.args[ci],caller.args[ri]])
        self.p4r_table_rows(info,surrogate,alias,content.get('pagination'))
        return surrogate,caller.args[ci]

    def p4r_scalar(self,ts,bindings):
        ts=self.p3_unwrap(ts);ms=matching(ts);q=None;colon=None;depth=0;at=0
        while at<len(ts):
            if ts[at].value in {'(','[','{'}:at=ms[at]+1;continue
            if ts[at].value=='?':
                if q is None:q=at
                depth+=1
            elif ts[at].value==':'and q is not None:
                depth-=1
                if depth==0:colon=at;break
            at+=1
        if q is not None and colon is not None:
            truth=self.p4_rep_condition(ts[:q],bindings)
            if type(truth)is not bool:raise Unsupported('Resource query route selector is not fixed by the actual literal UI argument')
            return self.p4r_scalar(ts[q+1:colon]if truth else ts[colon+1:],bindings)
        return self.p4_rep_value(ts,bindings)

    def p4r_object_capture(self,info,state):
        hits=[]
        for ts in info['model'].lists:
            ms=matching(ts)
            for i,t in enumerate(ts[:-3]):
                if t.value!=state or[x.value for x in ts[i+1:i+3]]!=['=','{']or info['model'].enclosing_function(t.start)is not info['function']:continue
                end=ms[i+2]
                if not self.p2_direct_statement(info['body'],t.start):
                    guards=self.p4_rep_call_guards(info,Call((),ts,i,i,i,[]))
                    if guards or not self.p4_rep_direct_body_token(info['model'],info['function'],t):raise Unsupported('Resource state capture is deferred or behind an unproved branch')
                fields={}
                for p in split_tokens(ts[i+3:end]):
                    if len(p)==1 and p[0].kind=='id':fields[p[0].value]=p
                    elif len(p)>=3 and p[1].value==':'and p[0].kind=='id':fields[p[0].value]=p[2:]
                    else:raise Unsupported('Resource mode capture has unknown/spread/computed fields')
                hits.append((t,fields))
        if len(hits)!=1:raise Unsupported('Resource opening lacks one original actual object state capture')
        self.p4_no_write_contexts(info['model'],{state},info['function'].body_start,info['function'].body_end,[hits[0][0]])
        return hits[0]

    def p4r_guard(self,info,condition,before):
        expected=[t.value for t in lex(condition)];body=info['body'];ms=matching(body);hits=[]
        for i,t in enumerate(body[:-1]):
            if t.value!='if'or body[i+1].value!='('or t.start>=before:continue
            end=ms[i+1]
            if[x.value for x in body[i+2:end]]==expected and end+1<len(body)and body[end+1].value=='return':hits.append(body[i+2:end])
        if len(hits)!=1:raise Unsupported('Resource captured save lacks its original fail-closed guard: '+condition)
        return hits[0]

    def p4r_control_templates(self,info,entry,alias):
        content=entry.get('content',{});kind=content.get('kind','table')
        if kind=='wrapper_toolbar':
            renderer=self.p4r_table(info,content,alias);wrapper=self.p4r_function(content.get('wrapper'),info['function'].parent);index=content.get('control_argument_index');calls_=[c for c in info['model'].calls if info['model'].enclosing_function(c.start)is info['function']and c.path==(wrapper['symbol'],)]
            if len(calls_)!=1 or type(index)is not int or not 0<=index<len(calls_[0].args)or len(calls_[0].args[index])!=1 or calls_[0].args[index][0].kind not in {'string','template'}:raise Unsupported('Resource original wrapper toolbar has different literal control argument')
            slot=self.p4_flow_ui_slot(content.get('ui_slot'));toolbars=[c for c in wrapper['model'].calls if wrapper['model'].enclosing_function(c.start)is wrapper['function']and c.path==(alias,'toolbar')and len(c.args)>slot['index']and[x.value for x in c.args[slot['index']]]==[wrapper['params'][index]]]
            if slot['function'].symbol!='toolbar'or slot['index']!=0 or len(toolbars)!=1:raise Unsupported('Resource wrapper does not consume original control in its real active toolbar slot')
            return renderer,[calls_[0].args[index][0]]
        if kind=='table':
            renderer=self.p4r_table(info,content,alias);return renderer,[renderer['template']]
        if kind not in {'toolbar','toolbar_value'}:raise Unsupported('Resource form opening requires an original table/toolbar control')
        slot=self.p4_flow_ui_slot(content.get('ui_slot'));index=content.get('argument_index')
        if slot['function'].symbol!='toolbar'or slot['index']!=index or index not in {0,1}:raise Unsupported('Resource form toolbar slot differs from the real active UI implementation')
        roots=self.p4r_literal_templates(info,self.p4r_return(info));hits=[]
        for root in roots:
            for k,part in root.parts:
                if k!='expression'or not self.p1_active_part(root,part):continue
                cs=[c for c in calls(part)if c.index==0 and c.end_index==len(part)-1 and c.path==(alias,'toolbar')]
                if len(cs)==1 and len(cs[0].args)>index:
                    ts=cs[0].args[index]
                    if len(ts)==1 and ts[0].kind=='template':hits.append(ts[0])
                    elif kind=='toolbar_value'and len(ts)==1 and ts[0].kind in {'string','id'}:
                        if ts[0].kind=='string':hits.append(ts[0])
                        else:
                            token,value=self.p4r_const(info,ts[0].value,cs[0].start);self.p4_no_write_contexts(info['model'],{ts[0].value},token.start-1,info['function'].body_end,[token]);self.p2_protected_escapes(info['model'],{ts[0].value},token.start-1,info['function'].body_end,[token,ts[0]],label='Resource original toolbar value');hits.append(value)
                    else:raise Unsupported('Resource toolbar mode profile requires its actual literal/const HTML value')
        if len(hits)!=1:raise Unsupported('Resource opening toolbar result is not in actual returned active HTML')
        return None,hits

    def p4r_mode_flow(self,flow):
        if[x.get('kind')for x in flow['flow_steps']]!=['render_to_original_mode_open','captured_mode_to_layer','layer_to_original_result']:raise Unsupported('Resource mode flow has incomplete or unused source edges')
        entry,event,result=flow['entry_binding'],flow['event_binding'],flow['result_binding'];mode=entry.get('mode');state=entry.get('state_symbol');route=self.p4_flow_route(entry.get('route'));pub=self.p4_flow_export(entry.get('page_export'));render=self.p4r_function(entry.get('render'),pub['parent']);self.p4r_page_route(pub,route)
        if render['function']is not pub['function']or mode not in {'view','create','edit','remove','import'}or not IDENT.fullmatch(state or ''):raise Unsupported('Resource mode flow has different actual render/mode/state')
        alias=entry.get('ui_alias');self.p4_flow_ui_alias(entry.get('ui_alias_evidence'),alias,pub['parent'],render['function'].start)
        claimed=self.proof['source_chain']['entry']
        if claimed.get('event')!=event.get('event')or normalized_callee(claimed.get('callee'))!=normalized_callee(event.get('callee')):raise Unsupported('Resource mode original event claim differs from its actual selected control')
        renderer,templates=self.p4r_control_templates(render,entry,alias);ee,args=self.p4r_conditional_event(templates,event,pub['parent']);opening=self.p4r_function(event.get('opening'),pub['parent']);owner=self.p4_flow_export(event.get('owner_binding'))
        if owner['function']is not opening['function']or owner['callee']!=normalized_callee(event.get('callee')):raise Unsupported('Resource form opening is not its actual published original handler')
        # Only this original three-argument mode/id/context handler family.
        if mode=='import':
            if opening['params']!=['key']or len(args)!=1:raise Unsupported('Resource import opening loses its original context argument')
        elif opening['params']!=(['mode','id','scope']if entry.get('draw_variant')=='number_pool'else['mode','id','key'])or len(args)!=3 or self.p4r_scalar(args[0],{})!=mode:raise Unsupported('Resource mode/id/context UI argument slots differ from the actual source')
        if mode=='import':pass
        elif renderer:
            if self.p4_identity_origin(args[1],renderer['params'],renderer['key'])is None:raise Unsupported('Resource mode opening uses a different row identity')
        elif mode!='create'or self.p4r_scalar(args[1],{})is not None:raise Unsupported('Resource new-object toolbar has an unrelated identity or mode')
        self.p4_no_write_contexts(opening['model'],set(opening['params']),opening['function'].body_start,opening['function'].body_end)
        capture,fields=self.p4r_object_capture(opening,state)
        expected={'mode':['mode'],'id':['row','?.','id'],'context':['catalog','.','context','||','key','(',')'],'scope':['key','(',')'],'revision':['catalog','.','revision']}if entry.get('draw_variant')=='number_pool'else{'mode':['mode'],'id':['row','?.','id'],'context':['copy','(','context','(',')',')'],'key':['scopeKey','(',')'],'revision':['catalog','.','revision']}
        if mode=='import':expected={'mode':["'import'"],'context':['copy','(','context','(',')',')'],'key':['scopeKey','(',')'],'revision':['catalog','.','revision'],'selected':['[',']']}
        for name,value in expected.items():
            if[x.value for x in fields.get(name,[])]!=value:raise Unsupported('Resource original mode capture changes its '+name+' provenance')
        # Actual input ID is resolved in the captured current catalog.  The
        # caller cannot replace it with a metadata ID or a later lookup.
        if mode!='import':self.p4_execution_pattern(opening,"const row=mode==='create'?null:catalog.rows.find(item=>String(item.id)===String(id));"if entry.get('draw_variant')=='number_pool'else"const row=mode==='create'?null:catalog.rows.find(item=>text(item.id)===text(id));",root=True)
        draw=self.p4r_function(entry.get('draw'),pub['parent']);dc=[c for c in opening['model'].calls if opening['model'].enclosing_function(c.start)is opening['function']and c.path==(draw['symbol'],)and not any(c.args)and c.start>capture.start]
        if len(dc)!=1 or not self.p2_direct_statement(opening['body'],dc[0].start):raise Unsupported('Resource original capture is not consumed by its actual layer producer')
        layer_slot=self.p4_flow_ui_slot(entry.get('layer_slot'));layers=[c for c in draw['model'].calls if draw['model'].enclosing_function(c.start)is draw['function']and c.path==(alias,'openLayer')and len(c.args)>1 and len(c.args[1])==1 and c.args[1][0].kind=='template']
        if layer_slot['function'].symbol!='openLayer'or layer_slot['index']!=1 or len(layers)!=1:raise Unsupported('Resource mode form lacks its real active layer HTML sink')
        layer=layers[0];out=self.p4r_function(result)
        if result.get('kind')=='mode_detail':
            if mode!='view':raise Unsupported('Resource details cannot borrow a write-mode form')
            self.p4_execution_pattern(draw,"const body=editor.mode==='view'?details():editor.mode==='remove'?`<p>确认删除号码池 <strong>${esc(editor.row.name)}</strong>？</p><p class=\"number-pool-note\">删除后，任务将无法再选用该池。已被任务引用或正在使用的默认池不可删除。</p>`:form();")if entry.get('draw_variant')=='number_pool'else self.p4_execution_pattern(draw,"else if(editor.mode==='view')body=details();")
            self.p4_execution_pattern(draw,"const title={create:'新增分机',edit:'编辑分机',view:'分机详情',remove:'删除分机',import:'从 AliCti 导入分机'}[editor.mode];")if entry.get('draw_variant')=='extension'else None
            if out['symbol']!='details':raise Unsupported('Resource view result is not the original details producer')
            # The selected body enters active layer text, not an attribute or
            # unrelated return value. `button` stays empty for the view path.
            html,parts=template_markup(layer.args[1][0]);parser=TableHTMLContext(parts);parser.feed(html);parser.close()
            if not any(k in parser.active and[x.value for x in v]==['body']for k,v in parts.items()):raise Unsupported('Resource details body is not consumed in active layer HTML')
        elif result.get('kind')in {'mode_save','mode_import_followup'}:
            if mode not in {'create','edit','remove','import'}or out['symbol']!='save':raise Unsupported('Resource save result does not match its original write mode')
            save_event=result.get('save_event',{});save_template=self.p4r_mode_save_template(layer.args[1][0],draw,state,mode)
            se,save_args=self.p4r_html_event_arguments([save_template],save_event,pub['parent'])
            save_owner=self.p4_flow_export(save_event.get('owner_binding'))
            if save_args and any(save_args)or save_owner['function']is not out['function']or save_owner['callee']!=normalized_callee(save_event.get('callee')):raise Unsupported('Resource layer save control is not the actual original save')
            self.p4_execution_pattern(out,'const active=editor;',root=True)
            commit=result.get('commit',{});method={'create':'create','edit':'update','remove':'remove','import':'importExisting'}[mode];factory=self.p4_flow_factory(commit.get('factory'));component=self.p4_flow_export(commit.get('publication'))
            if factory['owner']!=component['canonical_callee'][:-1]and('window',)+factory['owner']!=component['canonical_callee'][:-1]:raise Unsupported('Resource save component receiver differs from its original factory')
            calls_=[c for c in out['model'].calls if out['model'].enclosing_function(c.start)is out['function']and c.path==(factory['symbol']+'()',method)]
            if len(calls_)!=1:raise Unsupported('Resource save lacks its actual selected mode component commit')
            call=calls_[0];self.p4r_guard(out,"!active||active.pending||active.mode==='view'",call.start);context=self.p4r_guard(out,'!allowed(active.key)',call.start);revision=self.p4r_guard(out,'current.revision!==active.revision',call.start)
            self.p4_execution_pattern(out,'const opts={expectedContext:copy(active.context),expectedRevision:active.revision};',root=True)
            if not call.args or[x.value for x in call.args[-1]]!=['opts']or mode in {'edit','remove'}and[x.value for x in call.args[0]]!=['active','.','id']:raise Unsupported('Resource selected save loses the captured original identity/context/revision')
            # Original else-if or ternary discriminant must select this exact
            # method using the same captured state mode.
            if entry.get('draw_variant')=='extension':
                self.p4_execution_pattern(out,"if(active.mode==='create')result=await service().create(input,opts);else if(active.mode==='edit')result=await service().update(active.id,input,opts);else if(active.mode==='remove')result=await service().remove(active.id,opts);else if(active.mode==='import')result=await service().importExisting(active.selected.slice(),opts);")
            elif entry.get('draw_variant')=='time_condition':
                self.p4_execution_pattern(out,"const result=active.mode==='create'?await service().create(prepared.input,opts):active.mode==='edit'?await service().update(active.id,prepared.input,opts):await service().remove(active.id,opts);")
                projection=self.p4r_function(result.get('input_projection'),pub['parent'])
                if projection['params']!=['active']:raise Unsupported('Resource prepared input has a different original captured-state argument')
                self.p4_no_write_contexts(projection['model'],{'active'},projection['function'].body_start,projection['function'].body_end)
                self.p2_protected_escapes(projection['model'],{'active'},projection['function'].body_start,projection['function'].body_end,label='Resource prepared-input captured identity')
            else:raise Unsupported('Resource captured-mode save variant is not one of the original finite source structures')
            self.p4r_save_capture_guard(out,state,projection if entry.get('draw_variant')=='time_condition'else None)
            self.p4r_guard(out,'!result?.ok',out['function'].body_end)
            if not any(c.path==('showToast',)and c.start>call.end and out['model'].enclosing_function(c.start)is out['function']for c in out['model'].calls):raise Unsupported('Resource selected save lacks its actual visible local result feedback')
        else:raise Unsupported('Resource mode result variant is not implemented')
        te=self.evidence(self.proof['source_chain']['terminal'].get('evidence'))
        if mode=='import':
            if result.get('kind')!='mode_import_followup'or not te.contains(opening['function']):raise Unsupported('Resource import cannot replace its original opening observation with save')
            self.p4r_guard(opening,'!allowed(key)||!superUser()',opening['function'].body_end)
            self.p4r_guard(out,"active.mode==='import'&&!active.selected.length",call.start)
            if[x.value for x in call.args[0]]!=['active','.','selected','.','slice','(',')']:raise Unsupported('Resource import loses its original selected ID copy')
            selector=self.p4r_function(result.get('selection_handler'),pub['parent']);self.p4_execution_pattern(selector,"if(!editor||editor.mode!=='import'||editor.pending||!allowed(editor.key))return;",root=True);self.p4_execution_pattern(selector,"const candidate=editor.candidates?.rows?.find(row=>text(row.id)===text(id));",root=True)
            form=self.p4r_function(result.get('selection_form'),pub['parent']);self.p4_execution_pattern(draw,"else if(editor.mode==='import'){body=importForm();button='导入所选分机';blocked=!editor.candidates?.ok||!editor.candidates.rows.length;}")
            if form['symbol']!='importForm'or selector['symbol']!='selectCandidate':raise Unsupported('Resource import selected IDs are from different original controls')
            out['event_evidence']=se;out['capture_proofs']={'context':fields['context'],'mode':fields['mode'],'selection_ids':call.args[0],'revision':fields['revision']}
            ce=self.p4r_function(result.get('component'),component['parent']);ce.update(call=call,publication=component,factory=factory)
            if ce['function']is not component['function']:raise Unsupported('Resource import result differs from the original published component')
            verified=self.p4_captured_user_save_contract(flow,opening,out,ce)
            return dict(entry_evidence=ee,terminal_evidence=te,terminal_symbol=opening['symbol'],result_evidence=ce['evidence'],result_symbol=ce['symbol'],followup_verified=verified)
        if not te.contains(out['function']):raise Unsupported('Resource mode result moved from the original observation anchor')
        return dict(entry_evidence=ee,terminal_evidence=te,terminal_symbol=out['symbol'],result_evidence=out['evidence'],result_symbol=out['symbol'],followup_verified=False)

    def p4r_mode_condition(self,tokens,state,mode):
        tokens=self.p3_unwrap(tokens);v=[x.value for x in tokens]
        if len(tokens)==5 and v[:3]==[state,'.','mode']and v[3]in {'===','!=='}:
            value=self.p4_rep_literal(tokens[4:]);return(mode==value)if v[3]=='==='else(mode!=value)
        if tokens and tokens[0].value=='[':
            end=matching(tokens)[0]
            if v[end+1:]==['.','includes','(',state,'.','mode',')']:
                values=[self.p4_rep_literal(x)for x in split_tokens(tokens[1:end])];return mode in values
        return self.p4_rep_condition(tokens,{})

    def p4r_mode_button(self,draw,state,mode,name):
        matches=[]
        for ts in draw['model'].lists:
            ms=matching(ts)
            for i,t in enumerate(ts[:-2]):
                if t.value!=name or ts[i+1].value!='='or draw['model'].enclosing_function(t.start)is not draw['function']:continue
                end=i+2
                while end<len(ts)and ts[end].value not in {';',','}:end=ms[end]+1 if ts[end].value in {'(','[','{'}else end+1
                rhs=ts[i+2:end]
                try:
                    if len(rhs)==1:value=self.p4_rep_literal(rhs)
                    else:
                        q=next(j for j,x in enumerate(rhs)if x.value=='?');colon=next(j for j in range(q+1,len(rhs))if rhs[j].value==':');truth=self.p4r_mode_condition(rhs[:q],state,mode)
                        if type(truth)is not bool:continue
                        value=self.p4_rep_literal(rhs[q+1:colon]if truth else rhs[colon+1:])
                except (Unsupported,StopIteration):continue
                if not isinstance(value,str)or not value:continue
                fake=Call((),ts,i,i,i,[]);guards=self.p4_rep_call_guards(draw,fake)
                if not guards or any(self.p4r_mode_condition(c,state,mode)is not positive for c,positive in guards):continue
                matches.append(t)
        if len(matches)!=1:raise Unsupported('Resource selected write mode does not produce one original nonempty save button')
        return matches[0]

    def p4r_mode_save_template(self,root,draw,state,mode):
        self.p4r_mode_button(draw,state,mode,'button');found=[]
        for kind,part in root.parts:
            if kind!='expression'or not self.p1_active_part(root,part):continue
            if len(part)==5 and part[0].value=='button'and part[1].value=='?'and part[2].kind=='template'and part[3].value==':'and self.p1_literal(part[4:])=='':found.append(part[2])
        if len(found)!=1:raise Unsupported('Resource selected save control is not in its actual active button branch')
        return found[0]

    def p4r_conditional_event(self,templates,event,parent):
        hits=[]
        for root in templates:
            nodes=root if isinstance(root,list)else[root];leaves=[t for ts in iter_lists(nodes)for t in ts if t.kind in {'template','string'}]
            for leaf in leaves:
                try:
                    ee,args=self.p4r_html_event_arguments([leaf],event,parent)
                    html,expressions=self.p4_rep_markup(nodes,needle=(leaf.start,leaf.end),conditional_branches=True);parser=TableHTMLContext(expressions);parser.feed(html);parser.close()
                    if not any(key in parser.active and value and(value[0].start,value[-1].end)==(leaf.start,leaf.end)for key,value in expressions.items()):continue
                    hits.append((leaf.start,ee,args))
                except Unsupported:continue
        unique={start:(e,args)for start,e,args in hits}
        if len(unique)!=1:raise Unsupported('Resource mode control is absent, inert, statically dead or ambiguous in its actual returned HTML')
        return next(iter(unique.values()))

    def p4r_captured_escapes(self,info,name,allowed,allowed_methods=()):
        model=info['model'];first,last=info['function'].body_start,info['function'].body_end
        for ts in model.lists:
            for i,t in enumerate(ts):
                if t.kind!='id'or t.value!=name or not(first<t.start<last)or i and ts[i-1].value in {'.','?.'}or t in allowed:continue
                p=path_at(ts,i,False)
                if not p or len(p[0])!=1:continue
                if i+1<len(ts)and ts[i+1].value in {'=','+=','-=','++','--'}:continue
                if self.p2_presence_guard(model,t):continue
                raise Unsupported('Resource captured identity/context has an unproved whole-state copy, forwarding or bare use')
        for call in model.calls:
            if not(first<call.start<last):continue
            if call.path[:1]==(name,):
                if any(call is allowed_call for allowed_call in allowed_methods):continue
                # The original value form is editable; its value-field reads
                # are distinct from captured object identity/context/version.
                # A selection slice copies IDs and never mutates that source.
                if name=='active'and call.path[:2]==(name,'values')and len(call.path)>2:continue
                if call.path==(name,'selected','slice')and not any(call.args):continue
                raise Unsupported('Resource captured state invokes an unproved identity-affecting method')
            if any(len(arg)==1 and arg[0].kind=='id'and arg[0].value==name and arg[0]not in allowed for arg in call.args):raise Unsupported('Resource captured identity/context is forwarded to an unproved helper')

    def p4r_save_capture_guard(self,out,state,projection=None):
        capture=self.p4_execution_pattern(out,'const active='+state+';',root=True)[1];value_reads=[];allowed=[capture]
        for ts in out['model'].lists:
            for i,t in enumerate(ts[:-2]):
                if not(out['function'].body_start<t.start<out['function'].body_end):continue
                if t.value==state and ts[i+1].value in {'===','!=='}and ts[i+2].value=='active':allowed.append(ts[i+2])
                if t.value=='active'and[x.value for x in ts[i+1:i+3]]==['.','values']:value_reads.append(t)
        if projection:
            inputs=[c for c in out['model'].calls if out['model'].enclosing_function(c.start)is out['function']and c.path==(projection['symbol'],)and len(c.args)==1 and[x.value for x in c.args[0]]==['active']]
            if len(inputs)!=1:raise Unsupported('Resource prepared input does not consume the exact original captured state')
            allowed.append(inputs[0].args[0][0])
        self.p4_no_write_contexts(out['model'],{'active'},out['function'].body_start,out['function'].body_end,[capture]+value_reads)
        self.p4r_captured_escapes(out,'active',allowed)
        return capture

    def p4r_page_route(self,pub,route):
        owner=('window','Pages',route['route_page']);owners={tuple(pub['callee'][:-1]),tuple(pub['canonical_callee'][:-1])}|{tuple(x)for x in pub.get('forwarded_owners',[])}
        if owner not in owners and ('Pages',route['route_page'])not in owners:raise Unsupported('Resource actual published renderer is not bound to the selected original route page')

    def p4r_escaped_helper(self,spec,parent,use):
        if not isinstance(spec,dict):raise Unsupported('Resource event lacks its exact fixed-owner escaped helper evidence')
        e=self.evidence(spec.get('handler_helper_evidence'));name=spec.get('helper_call',{}).get('name');method=spec.get('helper_call',{}).get('literal_method')
        if not IDENT.fullmatch(name or '')or not IDENT.fullmatch(method or ''):raise Unsupported('Resource helper name/method is not literal')
        constants={}
        for item in spec.get('constant_keys',[]):
            key,value=self.p4_flow_const_literal(item,e.model,parent)
            if key in constants:raise Unsupported('Resource helper duplicate literal owner constant')
            constants[key]=value
        found=[]
        for ts in e.model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts[:-4]):
                if[x.value for x in ts[i:i+3]]!=[name,'=','(']or e.model.scope(t.start)is not parent:continue
                bindings=[(p,scope)for n,p,scope in e.model.variable_bindings()if n==name and scope is parent]
                if len(bindings)!=1:continue
                decl=next((x for x in ts if x.start==bindings[0][0]),None)
                if decl is None or decl.value!='const'or not(e.start<=decl.start<=t.start):continue
                close=ms[i+2];params=ts[i+3:close]
                if len(params)!=4 or params[0].kind!='id'or[x.value for x in params[1:3]]!=[',','...']or params[3].kind!='id'or ts[close+1].value!='=>':continue
                at=close+2
                if at+4>=len(ts)or ts[at].kind!='id'or[x.value for x in ts[at+1:at+2]]!=['(']or ts[at+2].kind!='template'or[x.value for x in ts[at+3:at+5]]!=[')',';']or ts[at+4].end>e.end:continue
                parts=ts[at+2].parts;prefix='';selected=None
                for j,(kind,value)in enumerate(parts):
                    if kind=='text':prefix+=value;continue
                    if[x.value for x in value]==[params[0].value]:selected=j;break
                    if len(value)==1 and value[0].value in constants:prefix+=constants[value[0].value];continue
                    selected=None;break
                if selected is None or not prefix.endswith('.')or len(parts)!=selected+4 or parts[selected+1]!=('text','(')or parts[selected+3]!=('text',')')or parts[selected+2][0]!='expression':continue
                owner=normalized_callee(prefix[:-1]);args=parts[selected+2][1];maps=[c for c in calls(args)if c.index==0 and c.path==(params[3].value,'map')and len(c.args)==1]
                if not owner or len(maps)!=1:continue
                mapper=maps[0].args[0]
                if len(mapper)!=8 or mapper[0].kind!='id'or[x.value for x in mapper[1:]]!=['=>','JSON','.','stringify','(',mapper[0].value,')']:continue
                tail=args[maps[0].end_index+1:]
                if len(tail)!=5 or[x.value for x in tail[:3]]!=['.','join','(']or literal(tail[3])!=','or tail[4].value!=')':continue
                allowed=self.p4_escape_callback_uses(spec,e,ts[at].value,parent)+self.p4r_escape_field_array_uses(spec,e,ts[at].value,parent)
                self.p4r_escape_alias(self.evidence(spec.get('escape_alias_evidence')),ts[at].value,parent,use,allowed);self.p2_no_owner_writes(e.model,[(name,)],[t]);self.no_shadow(e.model,name,parent,[use]);self.no_shadow(e.model,'JSON',None,[mapper[2].start],include_parent=True)
                found.append(dict(name=name,method=method,owner=owner,parent=parent,model=e.model))
        if len(found)!=1:raise Unsupported('Resource helper is not its exact immutable finite escaped fixed-owner/JSON-arguments constructor')
        return found[0]

    def p4r_escape_field_array_uses(self,spec,e,callback,parent):
        records=spec.get('escape_field_array_uses',[])
        if not isinstance(records,list)or len(records)>4:raise Unsupported('Resource field-array escape callback proof is not finite')
        allowed=[]
        for item in records:
            if not isinstance(item,dict)or item.get('version')!='bounded-guarded-field-array-escape@1':raise Unsupported('Unsupported explicit guarded field-array escape callback')
            info=self.p4r_function(item.get('producer'),parent);row=item.get('row_symbol');field=item.get('field');capture=item.get('captured_state')
            if info['model']is not e.model or not all(IDENT.fullmatch(x or '')for x in [row,field,capture]):raise Unsupported('Resource escape callback array is from a different original field producer')
            binding=self.p4_execution_pattern(info,'const '+row+'='+capture+'.row;',root=True)[1];self.p4_no_write_contexts(info['model'],{row},info['function'].body_start,info['function'].body_end,[binding])
            maps=[c for c in info['model'].calls if info['model'].enclosing_function(c.start)is info['function']and c.path==(row,field,'map')and len(c.args)==1 and[x.value for x in c.args[0]]==[callback]]
            if len(maps)!=1:raise Unsupported('Resource field-array display lacks one exact original escape map')
            call=maps[0];self.p4r_captured_escapes(info,row,[binding],[call]);guard=['Array','.','isArray','(',row,'.',field,')','&&',row,'.',field,'.','length','?']
            if not any([x.value for x in ts[i:i+len(guard)]]==guard and ts[i].start<call.start for ts in info['model'].lists for i in range(len(ts)-len(guard)+1)):raise Unsupported('Resource escape map lacks its actual array/type/nonempty conditional source')
            for ts in info['model'].lists:
                for i,t in enumerate(ts):
                    path=path_at(ts,i,False,partial=True)if t.kind=='id'else None
                    if path and(path[0][:2]==('Array','prototype')or path[0][:3]in{('window','Array','prototype'),('root','Array','prototype')}):raise Unsupported('Resource field-array callback source references Array.prototype')
            self.no_shadow(info['model'],callback,parent,[call.args[0][0].start]);self.no_shadow(info['model'],'Array',None,[call.start],include_parent=True);allowed.append(call.args[0][0])
        return allowed

    def p4r_escape_alias(self,e,name,parent,use,allowed):
        self.p4_flow_ui_alias(e.record,'ui',parent,use);declarations=[]
        for ts in e.model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts[:-4]):
                if t.value!='const'or i and ts[i-1].value=='('or e.model.scope(t.start)is not parent:continue
                at=i+1
                while at<len(ts)and ts[at].value!=';':at=ms[at]+1 if ts[at].value in {'(','[','{'}else at+1
                for part in split_tokens(ts[i+1:at]):
                    if[x.value for x in part]==[name,'=','ui','.','escape']and e.start<=t.start and part[-1].end<=e.end:declarations.append(part[0])
        if len(declarations)!=1:raise Unsupported('Resource escape is not its exact original same-scope const UI.escape alias')
        declaration=declarations[0];self.p2_no_owner_writes(e.model,[(name,),('ui','escape')],[declaration]);self.no_shadow(e.model,name,parent,[use])
        for ts in e.model.lists:
            for i,t in enumerate(ts):
                if t.kind!='id'or i and ts[i-1].value in {'.','?.'}:continue
                path=path_at(ts,i,False)
                if not path or path[0]not in {(name,),('ui','escape')}:continue
                end=path[1]
                if t is declaration or any(t is token for token in allowed)or i>=2 and ts[i-1].value=='='and ts[i-2]is declaration:continue
                if end<len(ts)and ts[end].value=='(':continue
                raise Unsupported('Resource fixed pure escape helper has an unproved bare copy or callback use')

    def p4r_return_guard(self,info,condition,before):
        wanted=[x.value for x in lex(condition)];body=info['body'];ms=matching(body);found=[]
        for i,t in enumerate(body[:-1]):
            if t.value!='if'or body[i+1].value!='('or t.start>=before:continue
            close=ms[i+1]
            if[x.value for x in body[i+2:close]]!=wanted:continue
            at=close+1
            if at<len(body)and body[at].value=='return':found.append(body[i+2:close]);continue
            if at<len(body)and body[at].value=='{'and([x.value for x in body[ms[at]-3:ms[at]]]==['return','false',';']or[x.value for x in body[ms[at]-2:ms[at]]]==['return',';']):found.append(body[i+2:close])
        if len(found)!=1:raise Unsupported('Resource captured operation lacks its actual returning failure guard: '+condition)
        return found[0]

    def p4r_critical_current(self,info,name,state,mutable,initials=()):
        allowed=list(initials);fields=[]
        for ts in info['model'].lists:
            for i,t in enumerate(ts[:-2]):
                if not(info['function'].body_start<t.start<info['function'].body_end):continue
                if t.value==state and ts[i+1].value in {'===','!=='}and ts[i+2].value==name:allowed.append(ts[i+2])
                if t.value==name and ts[i+1].value=='.'and ts[i+2].value in mutable:fields.append(t)
        self.p4_no_write_contexts(info['model'],{name},info['function'].body_start,info['function'].body_end,list(initials)+fields);self.p4r_captured_escapes(info,name,allowed)

    def p4r_provider_flow(self,flow):
        entry,event,result=flow['entry_binding'],flow['event_binding'],flow['result_binding'];kind=entry.get('operation');route=self.p4_flow_route(entry.get('route'));pub=self.p4_flow_export(entry.get('page_export'));render=self.p4r_function(entry.get('render'),pub['parent']);self.p4r_page_route(pub,route)
        if render['function']is not pub['function']or kind not in {'create','edit','RUNNING','STOPPED'}:raise Unsupported('Resource account flow has a different original page or operation')
        self.p4_flow_ui_alias(entry.get('ui_alias_evidence'),'ui',pub['parent'],render['function'].start);renderer,templates=self.p4r_control_templates(render,entry,'ui');ee,args=self.p4r_conditional_event(templates,event,pub['parent']);opening=self.p4r_function(event.get('opening'),pub['parent']);owner=self.p4_flow_export(event.get('owner_binding'));callee=normalized_callee(event.get('callee'));claimed=self.proof['source_chain']['entry']
        if owner['function']is not opening['function']or owner['callee']!=callee or callee!=normalized_callee(claimed.get('callee'))or event.get('event')!=claimed.get('event'):raise Unsupported('Resource account event/opening publication or original claim differs')
        if not args:raise Unsupported('Resource account original event arguments missing')
        self.p4_no_write_contexts(opening['model'],set(opening['params']),opening['function'].body_start,opening['function'].body_end)
        if kind=='create':
            if len(args)!=2 or self.p4r_scalar(args[0],{})!='':raise Unsupported('Resource account create does not use its actual empty new-object identity')
        else:
            if not renderer or self.p4_identity_origin(args[0],renderer['params'],'configId')!='row':raise Unsupported('Resource account event does not carry the same original row configId')
            self.p2_protected_escapes(render['model'],{renderer['params'][1]},renderer['template'].start,renderer['template'].end,label='Resource account row identity')
        out=self.p4r_function(result);layer_slot=self.p4_flow_ui_slot(entry.get('layer_slot'))
        if layer_slot['function'].symbol!='openLayer'or layer_slot['index']!=1:raise Unsupported('Resource account actual layer sink differs')
        if kind in {'create','edit'}:
            if opening['params']!=['configId','context']or out['symbol']!='save':raise Unsupported('Resource account form does not preserve original identity/context/save symbols')
            self.p4r_return_guard(opening,'!can()||(context!==undefined&&!sameContext(context))',opening['function'].body_end)
            self.p4_execution_pattern(opening,'const row=configId?service().find(configId):null;',root=True)
            capture,fields=self.p4r_const(opening,'current',opening['function'].body_end)
            props={p[0].value:p[2:]for p in split_tokens(fields[1:-1])if len(p)>=3 and p[1].value==':'}
            if[x.value for x in props.get('configId',[])]not in [[],['configId']]:raise Unsupported('Resource account form captures a different configId')
            # p1_props intentionally excludes shorthand; verify the actual
            # object source alongside the exact remaining captured fields.
            parts=split_tokens(fields[1:-1]);actual={p[0].value:p for p in parts if p}
            if[x.value for x in actual.get('configId',[])]!=['configId']or[x.value for x in props.get('context',[])]!=['currentContext','(',')']or[x.value for x in props.get('expectedVersion',[])]!=['row','?.','version']:raise Unsupported('Resource account form loses captured ID/context/version')
            self.p4_execution_pattern(opening,'editor=current;drawEditor(current);return true;',root=True)
            draw=self.p4r_function(entry.get('draw'),pub['parent'])
            if draw['params']!=['current']:raise Unsupported('Resource account layer producer receives a different captured object')
            layers=[c for c in draw['model'].calls if draw['model'].enclosing_function(c.start)is draw['function']and c.path==('ui','openLayer')and len(c.args)>1 and len(c.args[1])==1 and c.args[1][0].kind=='template']
            if len(layers)!=1:raise Unsupported('Resource account original editor has no unique real layer')
            save_event=result.get('save_event',{});se,save_args=self.p4r_html_event_arguments([layers[0].args[1][0]],save_event,pub['parent']);save_owner=self.p4_flow_export(save_event.get('owner_binding'))
            if save_owner['function']is not out['function']or save_owner['callee']!=normalized_callee(save_event.get('callee'))or any(save_args):raise Unsupported('Resource account actual editor save control differs from its original save')
            cap=self.p4_execution_pattern(out,'const current=editor;',root=True)[1]
            self.p4_execution_pattern(out,'const result=await service().save(payload,{...(current.configId?{configId:current.configId,expectedVersion:current.expectedVersion}:{}),context:current.context});')
            self.p4r_return_guard(out,'!sameContext(current.context)',out['function'].body_end);self.p4r_return_guard(out,'!result.ok',out['function'].body_end);self.p4r_critical_current(out,'current','editor',{'values','errors','busy'},[cap])
        else:
            if opening['params']!=['configId','nextStatus','context']or out['symbol']!='applyStatus'or len(args)!=3:raise Unsupported('Resource account status action does not preserve original arguments/result')
            row=renderer['params'][1]
            self.p4r_return_guard(opening,"!can()||!sameContext(context)||!['RUNNING','STOPPED'].includes(nextStatus)||statusDialog?.busy",opening['function'].body_end)
            self.p4r_return_guard(opening,'!row||row.status===nextStatus',opening['function'].body_end)
            if[x.value for x in args[1]]!=[row,'.','status','===',"'RUNNING'",'?',"'STOPPED'",':',"'RUNNING'"]:raise Unsupported('Resource account requested status is not the exact original row-state toggle')
            self.p4_execution_pattern(opening,'const row=service().find(configId);',root=True);self.p4_execution_pattern(opening,'const current={configId,status:nextStatus,expectedVersion:row.version,context:currentContext(),busy:false};statusDialog=current;',root=True)
            if kind=='RUNNING':self.p4_execution_pattern(opening,"if(nextStatus==='RUNNING')return applyStatus(current);")
            else:
                layers=[c for c in opening['model'].calls if opening['model'].enclosing_function(c.start)is opening['function']and c.path==('ui','openLayer')and len(c.args)>1 and len(c.args[1])==1 and c.args[1][0].kind=='template']
                if len(layers)!=1:raise Unsupported('Resource account disable has no original confirmation layer')
                confirm=result.get('confirmation',{});ce,confirm_args=self.p4r_html_event_arguments([layers[0].args[1][0]],confirm,pub['parent']);confirm_fn=self.p4r_function(confirm.get('handler'),pub['parent']);cp=self.p4_flow_export(confirm.get('owner_binding'))
                if any(confirm_args)or cp['function']is not confirm_fn['function']or cp['callee']!=normalized_callee(confirm.get('callee')):raise Unsupported('Resource account confirmation event is not its actual original handler')
                self.p4_execution_pattern(confirm_fn,'return statusDialog?applyStatus(statusDialog):false;',root=True)
            self.p4_execution_pattern(out,'const result=await service().setStatus(current.configId,current.status,{expectedVersion:current.expectedVersion,context:current.context});')
            self.p4r_return_guard(out,'!current||current.busy||!sameContext(current.context)',out['function'].body_end);self.p4r_return_guard(out,'!result.ok',out['function'].body_end);self.p4r_critical_current(out,'current','statusDialog',{'busy'})
        component=self.p4_flow_export(result.get('commit_publication'));factory=self.p4_flow_factory(result.get('factory'))
        if factory['owner']!=component['canonical_callee'][:-1]:raise Unsupported('Resource account save/status component receiver differs')
        target_method='save'if kind in {'create','edit'}else'setStatus';actual=[c for c in out['model'].calls if out['model'].enclosing_function(c.start)is out['function']and c.path==(factory['symbol']+'()',target_method)]
        if len(actual)!=1 or component['callee'][-1]!=target_method:raise Unsupported('Resource account selected actual component result is missing')
        component_info=self.p4r_function(result.get('component'),component['parent']);transaction=self.p4r_function(result.get('transaction'),component['parent'])
        if component_info['function']is not component['function']:raise Unsupported('Resource account component result is not the actual published implementation')
        self.p4_execution_pattern(component_info,'return transact(input,options,false);'if target_method=='save'else'return transact({status},{...options,configId},true);',root=True)
        transfers=[c for c in component_info['model'].calls if component_info['model'].enclosing_function(c.start)is component_info['function']and c.path==(transaction['symbol'],)]
        if len(transfers)!=1:raise Unsupported('Resource account component result is not transferred to its actual local transaction')
        self.no_shadow(component_info['model'],transaction['symbol'],component['parent'],[transfers[0].start])
        if[x.get('kind')for x in flow['flow_steps']]!=['original_account_control','captured_account_state','actual_account_result']:raise Unsupported('Resource account flow has unconsumed source edges')
        te=self.evidence(self.proof['source_chain']['terminal'].get('evidence'))
        if not te.contains(out['function']):raise Unsupported('Resource account result changed its original observation anchor')
        return dict(entry_evidence=ee,terminal_evidence=te,terminal_symbol=out['symbol'],result_evidence=out['evidence'],result_symbol=out['symbol'],followup_verified=False)

    def p4r_scalar_html_helper(self,info,renderer,step):
        if step.get('kind')!='returned_scalar_html_helper':raise Unsupported('Resource scalar helper needs its actual returned HTML source edge')
        target=self.p4r_function(step.get('target'),info['function'].parent);parts=[part for kind,part in renderer['template'].parts if kind=='expression'];index=step.get('expression_index');identity_index=step.get('identity_argument_index')
        if type(index)is not int or not 0<=index<len(parts)or not self.p1_active_part(renderer['template'],parts[index]):raise Unsupported('Resource scalar helper output is not in actual active row HTML')
        expr=parts[index];cs=[c for c in calls(expr)if c.index==0 and c.end_index==len(expr)-1 and c.path==(target['symbol'],)]
        if len(cs)!=1 or len(cs[0].args)!=len(target['params'])or type(identity_index)is not int or not 0<=identity_index<len(target['params']):raise Unsupported('Resource scalar helper actual arguments or identity slot differ')
        if self.p4_identity_origin(cs[0].args[identity_index],renderer['params'],renderer['key'])is None:raise Unsupported('Resource scalar helper receives an unrelated actual row identity')
        self.p4_no_write_contexts(target['model'],set(target['params']),target['function'].body_start,target['function'].body_end);self.no_shadow(info['model'],target['symbol'],info['function'].parent,[cs[0].start])
        return target,target['params'][identity_index],self.p4r_literal_templates(target,self.p4r_return(target))

    def p4r_skill_form(self,flow):
        entry,event,result=flow['entry_binding'],flow['event_binding'],flow['result_binding'];operation=entry.get('operation');route=self.p4_flow_route(entry.get('route'));pub=self.p4_flow_export(entry.get('page_export'));render=self.p4r_function(entry.get('render'),pub['parent']);self.p4r_page_route(pub,route)
        if operation not in {'create','edit'}or render['function']is not pub['function']:raise Unsupported('Resource skill form has a different original operation/page')
        self.p4_flow_ui_alias(entry.get('ui_alias_evidence'),'ui',pub['parent'],render['function'].start);render['route_view']=route['route_view'];producer=self.p4r_view_delegate(render,flow['flow_steps'][0]);renderer,templates=self.p4r_control_templates(producer,entry,'ui');ee,args=self.p4r_conditional_event(templates,event,pub['parent']);opening=self.p4r_function(event.get('opening'),pub['parent']);owner=self.p4_flow_export(event.get('owner_binding'));out=self.p4r_function(result,pub['parent']);actual=self.proof['source_chain']['entry']
        if opening['symbol']!='openMapping'or opening['params']!=['id','context']or out['symbol']!='saveMapping'or out['params']!=['id']or owner['function']is not opening['function']or owner['callee']!=normalized_callee(event.get('callee'))or normalized_callee(event.get('callee'))!=normalized_callee(actual.get('callee'))or event.get('event')!=actual.get('event'):raise Unsupported('Resource skill original opening/save or event binding differs')
        self.p4r_escape_alias(self.evidence(event.get('identity_escape_alias_evidence')),'esc',pub['parent'],ee.start,[]);self.p2_escape(self.evidence(event.get('escape_implementation_evidence')),self.evidence(event.get('escape_export_evidence')))
        if operation=='create':
            if any(args):raise Unsupported('Resource skill create cannot borrow an existing ID')
        else:
            if not renderer or len(args)!=2:raise Unsupported('Resource skill edit lacks its original same-row/context arguments')
            ce=[c for c in calls(args[0])if c.index==0 and c.end_index==len(args[0])-1]
            if len(ce)!=1 or ce[0].path!=('esc',)or len(ce[0].args)!=1 or self.p4_identity_origin(ce[0].args[0],renderer['params'],'physicalGroupId')is None:raise Unsupported('Resource skill edit does not preserve the original table ID')
            self.p4r_escape_alias(self.evidence(event.get('identity_escape_alias_evidence')),'esc',pub['parent'],ee.start,[]);self.p2_escape(self.evidence(event.get('escape_implementation_evidence')),self.evidence(event.get('escape_export_evidence')))
        self.p4_execution_pattern(opening,"const row=id?groupById(id):null;",root=True);self.p4_execution_pattern(opening,"editContext=contextKey();editingId=id||'';")
        self.p4r_guard(opening,'!canEdit(row)||(id&&!row)||(context&&context!==contextKey())',opening['function'].body_end);self.p4_no_write_contexts(opening['model'],{'id','context'},opening['function'].body_start,opening['function'].body_end)
        layer=self.p4_flow_ui_slot(entry.get('layer_slot'));layers=[c for c in opening['model'].calls if opening['model'].enclosing_function(c.start)is opening['function']and c.path==('ui','openLayer')and len(c.args)>1 and len(c.args[1])==1 and c.args[1][0].kind=='template']
        if layer['function'].symbol!='openLayer'or layer['index']!=1 or len(layers)!=1:raise Unsupported('Resource skill form lacks its actual original layer sink')
        save=result.get('save_event',{});se,save_args=self.p4r_html_event_arguments([layers[0].args[1][0]],save,pub['parent']);sp=self.p4_flow_export(save.get('owner_binding'))
        if sp['function']is not out['function']or sp['callee']!=normalized_callee(save.get('callee'))or len(save_args)!=1 or[x.value for x in save_args[0]]!=['esc','(','id','||',"''",')']:raise Unsupported('Resource skill actual user save loses the captured original ID')
        self.p4r_guard(out,"!editContext||editContext!==contextKey()||editingId!==(id||'')||!canEdit(row)||(id&&!row)",out['function'].body_end)
        self.p4_execution_pattern(out,"if(!persistRow('groups',next))return;");self.p4_execution_pattern(out,"AliCtiDemo.skillResource(next,row);")
        persist=self.p4r_function(result.get('persistence'),pub['parent']);calls_=[c for c in out['model'].calls if out['model'].enclosing_function(c.start)is out['function']and c.path==(persist['symbol'],)]
        if len(calls_)!=1:raise Unsupported('Resource skill original local persistence call is not unique')
        self.no_shadow(out['model'],persist['symbol'],pub['parent'],[calls_[0].start]);te=self.evidence(self.proof['source_chain']['terminal'].get('evidence'))
        if not te.contains(out['function'])or[x.get('kind')for x in flow['flow_steps'][1:]]!=['original_skill_control','scalar_id_context_capture','actual_skill_save']:raise Unsupported('Resource skill source/result edges or original anchor differ')
        return dict(entry_evidence=ee,terminal_evidence=te,terminal_symbol=out['symbol'],result_evidence=out['evidence'],result_symbol=out['symbol'],followup_verified=False)

    def p4r_agent_form(self,flow):
        entry,event,result=flow['entry_binding'],flow['event_binding'],flow['result_binding'];kind=entry.get('operation');route=self.p4_flow_route(entry.get('route'));pub=self.p4_flow_export(entry.get('page_export'));render=self.p4r_function(entry.get('render'),pub['parent']);self.p4r_page_route(pub,route)
        if kind not in {'create','phone','skill_add'}or render['function']is not pub['function']:raise Unsupported('Resource agent form differs from its original operation/page')
        render['route_view']=route['route_view'];producer=self.p4r_view_delegate(render,flow['flow_steps'][0]);self.p4_flow_ui_alias(entry.get('ui_alias_evidence'),'ui',pub['parent'],render['function'].start);renderer,templates=self.p4r_control_templates(producer,entry,'ui');ee,args=self.p4r_conditional_event(templates,event,pub['parent']);opening=self.p4r_function(event.get('opening'),pub['parent']);owner=self.p4_flow_export(event.get('owner_binding'));out=self.p4r_function(result,pub['parent']);actual=self.proof['source_chain']['entry']
        if owner['function']is not opening['function']or owner['callee']!=normalized_callee(event.get('callee'))or owner['callee']!=normalized_callee(actual.get('callee'))or event.get('event')!=actual.get('event'):raise Unsupported('Resource agent opening/event is not its actual original published handler')
        self.p4_no_write_contexts(opening['model'],set(opening['params']),opening['function'].body_start,opening['function'].body_end)
        if kind=='phone':
            if len(args)!=1 or not renderer or self.p4_identity_origin(args[0],renderer['params'],'contactCenterIdentityId')!='value':raise Unsupported('Resource phone form does not preserve the exact selected original seat identity')
        elif any(args):raise Unsupported('Resource fresh agent/skill form cannot borrow retry or unrelated identity arguments')
        layer=self.p4_flow_ui_slot(entry.get('layer_slot'));layers=[c for c in opening['model'].calls if opening['model'].enclosing_function(c.start)is opening['function']and c.path==('ui','openLayer')and len(c.args)>1 and len(c.args[1])==1 and c.args[1][0].kind=='template']
        if layer['function'].symbol!='openLayer'or layer['index']!=1 or len(layers)!=1:raise Unsupported('Resource agent opening does not consume the original form in its real layer')
        save=result.get('save_event',{});se,save_args=self.p4r_html_event_arguments([layers[0].args[1][0]],save,pub['parent']);sp=self.p4_flow_export(save.get('owner_binding'))
        if sp['function']is not out['function']or sp['callee']!=normalized_callee(save.get('callee'))or any(save_args):raise Unsupported('Resource agent layer save is not its actual original result handler')
        if kind=='create':
            if opening['symbol']!='openSingle'or out['symbol']!='createSingle':raise Unsupported('Resource new seat uses a different original opening/result')
            self.p4_execution_pattern(opening,'createContext=contextKey();createRequestId=crypto.randomUUID();creating=false;')
            self.p4r_guard(out,"createContext!==contextKey()||!tenant||!mayManage(tenant)||tenant.status!=='启用'||!tenant.capabilitySet.includes('CLOUD_CONTACT_CENTER')",out['function'].body_end)
            self.p4_execution_pattern(out,'const submittedContext=createContext;creating=true;',root=True)
            self.p4_execution_pattern(out,'const result=await AccountSeat.create({tenantId:tenant.tenantId,userName,cno,areaCode},{requestId:createRequestId,configurationRecordId:retrySyncId||undefined});',root=True)
            self.p4r_return_guard(out,'!result.ok',out['function'].body_end)
            # A failed optional skill assignment does not roll back the new
            # seat or become a fake all-or-nothing success.
            self.p4_execution_pattern(out,"const creationMessage=skillsSaved?'坐席已新增'+(selectedSkills.length?'，技能已分配':'，可稍后配置技能'):'坐席已新增，但技能分配未完成，请在坐席详情中重新配置';",root=True)
            component=self.p4_flow_export(result.get('component_publication'))
            if component['canonical_callee']!=('window','AccountSeat','create'):raise Unsupported('Resource new seat component is not the actual AccountSeat create implementation')
        elif kind=='phone':
            if opening['symbol']!='openPhoneConfig'or out['symbol']!='savePhoneConfig':raise Unsupported('Resource phone form uses a different original opening/result')
            cap,fields=self.p4r_object_capture(opening,'phoneDialog')
            expected={'id':['id'],'context':['contextKey','(',')'],'expectedContext':['configured','.','context'],'expectedRevision':['configured','.','revision'],'expectedDirectoryRevision':['choices','.','revision'],'expectedDirectoryContext':['choices','.','context']}
            if any([x.value for x in fields.get(k,[])]!=v for k,v in expected.items()):raise Unsupported('Resource phone capture loses seat/context/directory/version identity')
            request,value=self.p4r_const(out,'request',out['function'].body_end)
            if[x.value for x in value]!=['phoneDialog']:raise Unsupported('Resource phone save does not retain its actual captured dialog')
            self.p4r_guard(out,'!node||request.context!==contextKey()||!row||!mayManage(row)',out['function'].body_end)
            self.p4_execution_pattern(out,'result=await SeatPhoneConfig.save(request.id,node.value,{expectedContext:request.expectedContext,expectedRevision:request.expectedRevision,expectedDirectoryRevision:request.expectedDirectoryRevision,expectedDirectoryContext:request.expectedDirectoryContext});')
            self.p4r_guard(out,'phoneDialog!==request||request.context!==contextKey()',out['function'].body_end);self.p4r_critical_current(out,'request','phoneDialog',set(),[request]);component=self.p4_flow_export(result.get('component_publication'))
            if component['canonical_callee']!=('window','SeatPhoneConfig','save'):raise Unsupported('Resource phone actual local result receiver differs')
        else:
            if opening['symbol']!='openSkillAssign'or out['symbol']!='saveSkill':raise Unsupported('Resource skill assignment uses a different original opening/result')
            self.p4_execution_pattern(opening,"skillAssignContext=contextKey();skillAssignGroupId=fixedGroup?groupId:'';skillAssignIdentityId=identityId||'';",root=True)
            self.p4r_guard(out,"skillAssignContext!==contextKey()||!agent||!mayManage(agent)||agent.lifecycleStatus!=='已启用'",out['function'].body_end)
            self.p4_execution_pattern(out,'const agent=agentByIdentity(skillAssignIdentityId||document.getElementById(\'assignAgent\')?.value),level=document.getElementById(\'assignLevel\')?.value;',root=True)
            self.p4_execution_pattern(out,'if(!AliCtiDemo.saveSkills(agent,existing))return;',root=True)
            source=self.evidence(result.get('local_skill_result'));self.p4_execution_pattern(out,"showToast(CloudCallData.agentSkills.some(r=>r.identityId===agent.contactCenterIdentityId&&r.syncStatus==='待提交')?'新增技能已保存待提交，请在坐席下线后提交':'技能关联已保存','success');")
        if[x.get('kind')for x in flow['flow_steps'][1:]]!=['original_agent_control','captured_agent_form','actual_agent_user_save']:raise Unsupported('Resource agent original frame/source edges differ')
        te=self.evidence(self.proof['source_chain']['terminal'].get('evidence'))
        if not te.contains(out['function']):raise Unsupported('Resource agent result changes its original observation anchor')
        return dict(entry_evidence=ee,terminal_evidence=te,terminal_symbol=out['symbol'],result_evidence=out['evidence'],result_symbol=out['symbol'],followup_verified=False)

    def p4r_layer_call(self,info,slot):
        ui=self.p4_flow_ui_slot(slot);calls_=[c for c in info['model'].calls if info['model'].enclosing_function(c.start)is info['function']and c.path==('ui','openLayer')and len(c.args)>1 and len(c.args[1])==1 and c.args[1][0].kind=='template']
        if ui['function'].symbol!='openLayer'or ui['index']!=1 or len(calls_)!=1:raise Unsupported('Resource import has no unique actual original layer HTML sink')
        return calls_[0]

    def p4r_state_control(self,info,event,path,value,output):
        ee=self.evidence(event.get('evidence'));hits=[]
        for ts in info['model'].lists:
            ms=matching(ts)
            for i,t in enumerate(ts[:-2]):
                if t.value!=output or ts[i+1].value!='='or info['model'].enclosing_function(t.start)is not info['function']:continue
                end=i+2
                while end<len(ts)and ts[end].value!=';':end=ms[end]+1 if ts[end].value in {'(','[','{'}else end+1
                rhs=ts[i+2:end]
                if len(rhs)!=1 or rhs[0].kind not in {'string','template'}:continue
                try:e,args=self.p4r_html_event_arguments([rhs[0]],event,info['parent'])
                except Unsupported:continue
                call=Call((),ts,i,i,i,[]);guards=self.p4_rep_call_guards(info,call);matched=False
                for condition,positive in guards:
                    condition=self.p3_unwrap(condition);vals=[x.value for x in condition]
                    if len(condition)==5 and vals[:3]==list(path)and vals[3]in {'===','!=='}:
                        actual=self.p4_rep_literal(condition[4:]);truth=(type(actual)is type(value)and actual==value)==(vals[3]=='===')
                        if truth!=positive:raise Unsupported('Resource import control is in a different original stage/mode branch')
                        matched=True
                if matched:hits.append((e,args,t))
        if len(hits)!=1:raise Unsupported('Resource original stage/mode does not produce one actual selected user control')
        e,args,target=hits[0];self.p4r_state_output_stable(info,path,value,output,target)
        return e,args

    def p4r_owned_dom(self,template,identity):
        html,values=template_markup(template)
        class OwnedSlot(TableHTMLContext):
            def __init__(self,markers):super().__init__(markers);self.hits=[]
            def handle_starttag(self,tag,attrs):
                super().handle_starttag(tag,attrs)
                if dict(attrs).get('id')==identity:self.hits.append(not any(blocked for _,blocked in self.stack))
        parser=OwnedSlot(values);parser.feed(html);parser.close()
        if parser.hits!=[True]:raise Unsupported('Resource import DOM target is absent, duplicated or inside an inactive original layer ancestor')

    def p4r_component_import(self,flow):
        entry,event,result=flow['entry_binding'],flow['event_binding'],flow['result_binding'];kind=entry.get('operation');route=self.p4_flow_route(entry.get('route'));pub=self.p4_flow_export(entry.get('page_export'));render=self.p4r_function(entry.get('render'),pub['parent']);self.p4r_page_route(pub,route)
        if kind not in {'number','seat_existing','seat_batch','seat_verify'}or render['function']is not pub['function']:raise Unsupported('Resource import operation/page is not its original finite source family')
        render['route_view']=route['route_view'];step=flow['flow_steps'][0];producer=self.p4r_view_delegate(render,step)if step['kind']=='route_view_delegate'else self.p4r_returned_delegate(render,step);self.p4_flow_ui_alias(entry.get('ui_alias_evidence'),'ui',pub['parent'],render['function'].start);_,templates=self.p4r_control_templates(producer,entry,'ui');ee,args=self.p4r_conditional_event(templates,event,pub['parent']);opening=self.p4r_function(event.get('opening'));opub=self.p4_flow_export(event.get('owner_binding'));out=self.p4r_function(result);actual=self.proof['source_chain']['entry']
        if opening['function']is not opub['function']or opub['callee']!=normalized_callee(event.get('callee'))or normalized_callee(actual.get('callee'))!=opub['callee']or actual.get('event')!=event.get('event'):raise Unsupported('Resource import actual external control/opening differs from its original chain')
        self.p4_flow_ui_alias(entry.get('component_ui_alias_evidence'),'ui',opening['parent'],opening['function'].start);self.p4_no_write_contexts(opening['model'],set(opening['params']),opening['function'].body_start,opening['function'].body_end)
        if kind=='number':
            if len(args)!=1 or[x.value for x in args[0]]!=['esc','(','JSON','.','stringify','(','key',')',')']:raise Unsupported('Resource number import loses its actual serialized page context')
            self.p4r_escape_alias(self.evidence(event.get('identity_escape_alias_evidence')),'esc',producer['parent'],ee.start,[]);self.p2_escape(self.evidence(event.get('escape_implementation_evidence')),self.evidence(event.get('escape_export_evidence')))
            self.p4r_guard(opening,'!can()||context&&context!==ctx()',opening['function'].body_end);cap,fields=self.p4r_object_capture(opening,'state')
            expected={'context':['ctx','(',')'],'enterpriseId':['AppState','.','get','(',')','.','enterpriseId'],'step':['1'],'selected':['new','Map','(',')']}
            if any([x.value for x in fields.get(k,[])]!=v for k,v in expected.items()):raise Unsupported('Resource number import does not capture the original account/stage/selection')
            draw=self.p4r_function(entry.get('component_draw'),opening['parent']);self.p4r_import_checkbox(draw,result.get('selection_ui'),'number','AliCtiNumberImport',opening['parent']);self.p4_execution_pattern(opening,'fetchPage();render();return true;',root=True);layer=self.p4r_layer_call(draw,entry.get('layer_slot'));self.p4_execution_active_value(layer.args[1],'body');self.p4_execution_active_value(layer.args[1],'footer')
            se,save_args=self.p4r_state_control(draw,result.get('save_event'),('s','.','step'),2,'footer')
            if any(save_args):raise Unsupported('Resource original number import submit unexpectedly takes another object')
            next_=self.p4r_function(result.get('next'),opening['parent']);self.p4_execution_pattern(next_,"if(!valid()||state.step!==1||!state.selected.size||state.error)return;state.step=2;state.error='';render();",root=True)
            toggle=self.p4r_function(result.get('selection'),opening['parent']);self.p4_execution_pattern(toggle,'const row=state.rows.find(r=>String(r.id)===id);',root=True);self.p4_execution_pattern(toggle,'if(!row||issue(row)||existing(row,state.enterpriseId))return;',root=True);self.p4_execution_pattern(toggle,'if(state.selected.has(id))state.selected.delete(id);else state.selected.set(id,clone(row));')
            self.p4r_guard(out,'!valid()||state.step!==2',out['function'].body_end);self.p4_execution_pattern(out,'const s=state,rows=[...s.selected.values()];',root=True);self.p4_execution_pattern(out,'localStorage.setItem(key,JSON.stringify(saved.concat(added)));');self.p4_execution_pattern(out,"data.phoneNumbers.push(...added);data.numberOnboardingBatches.push(batch);s.result=added;s.step=3;s.error='';")
            if out['symbol']!='submit':raise Unsupported('Resource original number import result is not submit')
        else:
            requested='existing'if kind=='seat_existing'else'batch'
            self.p4r_escape_alias(self.evidence(result.get('identity_escape_alias_evidence')),'esc',opening['parent'],opening['function'].start,[]);self.p2_escape(self.evidence(result.get('escape_implementation_evidence')),self.evidence(result.get('escape_export_evidence')))
            if len(args)!=1 or self.p4r_scalar(args[0],{})!=requested:raise Unsupported('Resource seat import uses a different actual original opening mode')
            self.p4_execution_pattern(opening,"dialog={mode:mode==='batch'?'batch':'existing',context:context(),start:0,limit:10,plan:null,rows:[]};busy=false;",root=True);layer=self.p4r_layer_call(opening,entry.get('layer_slot'));self.p4r_owned_dom(layer.args[1][0],'seat-import-result')
            if kind=='seat_verify':
                pending=self.p4r_function(result.get('pending_renderer'),opening['parent']);self.p4_execution_pattern(opening,"if(dialog.mode==='existing')query();else renderPending();",root=True);self.p4_execution_pattern(pending,"if(!valid()||dialog.mode!=='batch')return;",root=True)
                # The original record-id button is returned by the actual
                # records.map/join and committed to the owned layer target.
                self.p4_execution_pattern(pending,"const pending=pendingRecords(),records=ui.sortByUpdated?.(pending,['at'])||pending,el=document.getElementById('seat-import-result');",root=True)
                maps=[c for c in pending['model'].calls if pending['model'].enclosing_function(c.start)is pending['function']and c.path==('records','map')and len(c.args)==1]
                if len(maps)!=1:raise Unsupported('Resource original pending journal has no actual record renderer')
                self.p4r_pending_dom(pending,maps[0]);callback=maps[0].args[0]
                if len(callback)!=3 or[x.value for x in callback[:2]]!=['record','=>']or callback[2].kind!='template':raise Unsupported('Resource pending record button is not its exact original returned template')
                se,verify_args=self.p4r_html_event_arguments([callback[2]],result.get('save_event'),opening['parent'])
                if len(verify_args)!=1 or[x.value for x in verify_args[0]]!=['esc','(','record','.','id',')']:raise Unsupported('Resource verification button loses the original pending record identity')
                self.p4r_guard(out,"busy||!valid()||dialog.mode!=='batch'",out['function'].body_end);self.p4_execution_pattern(out,'const record=pendingRecords().find(row=>row.id===id);',root=True);self.p4_execution_pattern(out,"saveJournal(journal.map(item=>item.id===id?next:item));")
                if out['symbol']!='verifyPending':raise Unsupported('Resource original journal verification result changed')
            else:
                se,save_args=self.p4r_html_event_arguments([layer.args[1][0]],result.get('save_event'),opening['parent'])
                if any(save_args):raise Unsupported('Resource original seat import submit takes an unrelated object')
                self.p4r_guard(out,'busy||!valid()',out['function'].body_end);self.p4_execution_pattern(out,"const captured=context(),tenant=currentTenant(),batch=dialog.mode==='batch';")
                self.p4_execution_pattern(out,"if(batch){preview();if(!dialog.plan)return;}")
                self.p4_execution_pattern(out,'const plan=dialog.plan,selected=batch?plan.cnos:[...document.querySelectorAll(\'[name="seat-import-selection"]:checked\')].map(el=>el.value);',root=True)
                preview=self.p4r_function(result.get('preview'),opening['parent']);selection=self.p4r_function(result.get('selection'),opening['parent']);self.p4_execution_pattern(opening,"if(dialog.mode==='existing')query();else renderPending();",root=True)
                self.p4r_guard(preview,"!valid()||dialog.mode!=='batch'",preview['function'].body_end);self.p4r_guard(selection,"!valid()||dialog.mode!=='existing'",selection['function'].body_end)
                if kind=='seat_existing':self.p4r_import_checkbox(selection,result.get('selection_ui'),'seat','AliCtiSeatImport',opening['parent'])
                self.p4_execution_pattern(out,"const saved=persistRaw(raw,tenant,batch?'batchCreate':'query/get');");self.p4_execution_pattern(out,"saveJournal([{...last,cnos:[...selected],at:new Date().toLocaleString('sv-SE')},...journal]);")
                if out['symbol']!='submit':raise Unsupported('Resource original seat import result changed')
            persist=self.p4r_function(result.get('persistence'),opening['parent']);self.p4_execution_pattern(persist,"return AccountSeat.persistAgent(a)?{ok:true,agent:a}:{ok:false,message:'本地保存失败，请查证后重试'};",root=True)
        save_pub=self.p4_flow_export(result.get('owner_binding'))
        if save_pub['function']is not out['function']or save_pub['callee']!=normalized_callee(result.get('save_event',{}).get('callee')):raise Unsupported('Resource import user result is not its actual published original handler')
        te=self.evidence(self.proof['source_chain']['terminal'].get('evidence'))
        if not te.contains(out['function'])or[x.get('kind')for x in flow['flow_steps'][1:]]!=['original_import_opening','captured_import_layer','original_selection_or_precheck','actual_import_user_result']:raise Unsupported('Resource import source edges or exact original anchor differ')
        return dict(entry_evidence=ee,terminal_evidence=te,terminal_symbol=out['symbol'],result_evidence=out['evidence'],result_symbol=out['symbol'],followup_verified=False)

    def p4r_import_checkbox(self,info,spec,kind,owner,parent):
        key='id'if kind=='number'else'cno';rowpath=['s','.','rows']if kind=='number'else['dialog','.','rows'];found=[]
        for call in info['model'].calls:
            if info['model'].enclosing_function(call.start)is not info['function']or call.path!=('ui','table')or len(call.args)<2 or[x.value for x in call.args[1]]!=rowpath:continue
            cols=call.args[0]
            if not cols or cols[0].value!='['or matching(cols).get(0)!=len(cols)-1:continue
            items=split_tokens(cols[1:-1]);props=self.p1_props(items[0])
            if self.p1_literal(props.get('key',[]))!=key:continue
            value=props.get('render',[]);vm=matching(value)
            if not value or value[0].value!='(':continue
            close=vm[0];params=[p[0].value for p in split_tokens(value[1:close])if len(p)==1 and p[0].kind=='id']
            if len(params)!=2 or[x.value for x in value[close+1:close+2]]!=['=>']:continue
            output=value[close+2:]
            if kind=='number':
                if len(output)!=1 or output[0].kind!='template':continue
                template=output[0]
                if[x.value for x in call.tokens[max(0,call.index-2):call.index]]!=['body','+=']:raise Unsupported('Resource number selection table is not appended to its actual active body')
                self.p4_rep_call_guards(info,call)
            else:
                if not output or output[0].value!='{'or matching(output).get(0)!=len(output)-1:continue
                body=output[1:-1];returns=[i for i,t in enumerate(body)if t.value=='return'];templates=[t for ts in iter_lists(body)for t in ts if t.kind=='template'and'name="seat-import-selection"'in ''.join(v for k,v in t.parts if k=='text')]
                if len(templates)!=1:continue
                template=templates[0];ret=[i for i in returns if i+5<len(body)and body[i+1].value=='exists'and body[i+2].value=='?'and body[i+3].kind=='string'and body[i+4].value==':'and body[i+5]is template]
                if len(ret)!=1:raise Unsupported('Resource existing-seat checkbox is not the actual original returned available branch')
                if[x.value for x in call.tokens[max(0,call.index-4):call.index]][-3:]!=['.','innerHTML','=']:raise Unsupported('Resource existing-seat selection table has no original owned DOM assignment')
                getters=[c for c in info['model'].calls if info['model'].enclosing_function(c.start)is info['function']and c.path==('document','getElementById')and len(c.args)==1 and self.p1_literal(c.args[0])=='seat-import-result'and c.end<call.start]
                if not getters:raise Unsupported('Resource existing-seat selection is not assigned to the layer-owned result element')
            self.p4_renderer_readonly(info['model'],template,params);html,expressions=template_markup(template)
            class InputSlot(TableHTMLContext):
                def __init__(self,m):super().__init__(m);self.inputs=[]
                def handle_starttag(self,tag,attrs):
                    super().handle_starttag(tag,attrs)
                    values=dict(attrs);style=values.get('style')or''
                    hidden='hidden'in values or'inert'in values or bool(re.search(r'(?:display\s*:\s*none|visibility\s*:\s*hidden|content-visibility\s*:\s*hidden)',style,re.I))
                    if tag=='input'and not hidden and not any(blocked for _,blocked in self.stack):self.inputs.append(values)
            parser=InputSlot(expressions);parser.feed(html);parser.close();name='aci-row'if kind=='number'else'seat-import-selection';inputs=[a for a in parser.inputs if a.get('type')=='checkbox'and a.get('name')==name]
            if len(inputs)!=1:raise Unsupported('Resource original selection checkbox is missing, inert, duplicated or has a different field name')
            if kind=='number':
                event=dict(spec['event'],event='change',callee=owner+'.toggle',evidence=spec['evidence']);_,args=self.p4r_html_event_arguments([template],event,parent)
                if len(args)!=1 or[x.value for x in args[0]]!=['String','(',params[0],')']:raise Unsupported('Resource number selection checkbox loses the original table ID')
                self.no_shadow(info['model'],'String',None,[template.start],include_parent=True)
            else:
                value=expressions.get(inputs[0].get('value'),[])
                if[x.value for x in value]!=['esc','(',params[0],')']:raise Unsupported('Resource existing-seat checkbox loses the original CNO text identity')
            self.p1_table_contract(self.evidence(spec.get('table_implementation_evidence')),self.evidence(spec.get('table_export_evidence')),key);found.append(call)
        if len(found)!=1:raise Unsupported('Resource import lacks its unique actual row selection table/control')

    def p4r_pending_dom(self,info,map_call):
        hits=[]
        for ts in info['model'].lists:
            ms=matching(ts)
            for i,t in enumerate(ts[:-4]):
                if[x.value for x in ts[i:i+4]]!=['el','.','innerHTML','=']or info['model'].enclosing_function(t.start)is not info['function']:continue
                end=i+4
                while end<len(ts)and ts[end].value!=';':end=ms[end]+1 if ts[end].value in {'(','[','{'}else end+1
                rhs=ts[i+4:end]
                if len(rhs)!=7 or[x.value for x in rhs[:4]]!=['records','.','length','?']or rhs[4].kind!='template'or rhs[5].value!=':'or self.p1_literal(rhs[6:])!='':raise Unsupported('Resource pending record list is not the actual original owned-DOM result')
                root=rhs[4];active=[]
                for kind,part in root.parts:
                    if kind!='expression'or not self.p1_active_part(root,part)or not part or part[0].start!=map_call.start:continue
                    cs=[c for c in calls(part)if c.index==0 and c.start==map_call.start]
                    if len(cs)!=1:continue
                    suffix=part[cs[0].end_index+1:]
                    if len(suffix)==5 and[x.value for x in suffix[:3]]==['.','join','(']and self.p1_literal(suffix[3:4])==''and suffix[4].value==')':active.append(part)
                if len(active)!=1:raise Unsupported('Resource pending record button output is unused or in an inactive original HTML slot')
                hits.append(t)
        if len(hits)!=1:raise Unsupported('Resource pending record output has no unique original owned DOM assignment')
        binding,_=self.p4r_const(info,'el',hits[0].start);allowed=[binding,hits[0]]
        for ts in info['model'].lists:
            for i,t in enumerate(ts[:-3]):
                if[x.value for x in ts[i:i+4]]==['if','(','el',')']and info['function'].body_start<t.start<hits[0].start:allowed.append(ts[i+2])
        self.p4_no_write_contexts(info['model'],{'el'},info['function'].body_start,info['function'].body_end,allowed);self.p4r_captured_escapes(info,'el',allowed)

    def p4r_boolean_branch(self,tokens,condition,required_control=None):
        wanted=[t.value for t in lex(condition)];found=[]
        def visit(ts):
            ts=self.p3_unwrap(ts)
            try:cond,yes,no=self.p4_execution_ternary(ts)
            except Unsupported:pass
            else:
                if[x.value for x in cond]==wanted and(required_control is None or any(required_control in t.value for part in iter_lists(yes)for t in part if t.kind in {'string','template'})):found.append((ts,yes,no))
            ms=matching(ts);i=0
            while i<len(ts):
                if ts[i].value in {'(','[','{'}:visit(ts[i+1:ms[i]]);i=ms[i]+1
                else:i+=1
        visit(tokens)
        if len(found)!=1:raise Unsupported('Resource original layer does not contain its one explicit selected boolean control branch')
        return found[0]

    def p4r_account_bind(self,flow):
        entry,event,result=flow['entry_binding'],flow['event_binding'],flow['result_binding'];route=self.p4_flow_route(entry.get('route'));pub=self.p4_flow_export(entry.get('page_export'));render=self.p4r_function(entry.get('render'),pub['parent']);self.p4r_page_route(pub,route);render['route_view']=route['route_view'];producer=self.p4r_view_delegate(render,flow['flow_steps'][0]);front=self.p4r_account_membership_entry(entry.get('membership'))
        if producer['function']is not self.p4r_function(entry['membership']['render'])['function']or render['function']is not pub['function']or route['route_view']!='accounts':raise Unsupported('Resource account binding is not in the original selected accounts renderer')
        claimed=self.proof['source_chain']['entry'];callee=normalized_callee(event.get('callee'))
        if callee!=normalized_callee(front['callee'])or callee!=normalized_callee(claimed.get('callee'))or event.get('event')!=claimed.get('event'):raise Unsupported('Resource account membership entry claim differs from its actual original row control')
        opening_pub=self.p4_flow_export(event.get('owner_binding'));fn,params,body=self.p4_flow_body(opening_pub);opening=dict(function=fn,params=params,body=body,model=opening_pub['model'],parent=opening_pub['parent'],evidence=opening_pub['evidence'],symbol='openAccountSeat')
        if params!=['id']or opening_pub['callee']!=callee:raise Unsupported('Resource membership opening has a different original parameter/publication')
        self.p4_no_write_contexts(opening['model'],{'id'},fn.body_start,fn.body_end);self.p4_execution_pattern(opening,'var member=CloudCallData.memberships.find(function(item){return item.membershipId===id;});',root=True);self.p4_execution_pattern(opening,'var tenant=member&&CloudCallRuntime.tenant(member.tenantId);',root=True);self.p4r_guard(opening,'!member||!tenant||member.tenantId!==selectedTenantId||!canEditMember(member)',fn.body_end);self.p4_execution_pattern(opening,'AccountSeat.open(member.accountId,member.tenantId,tenant.enterpriseId);',root=True)
        component_open=self.p4r_function(event.get('component_opening'));cp=self.p4_flow_export(event.get('component_publication'))
        if cp['function']is not component_open['function']or cp['canonical_callee']!=('window','AccountSeat','open'):raise Unsupported('Resource membership does not open the actual AccountSeat component')
        self.p4_execution_pattern(component_open,"const error=scopeError(tenantId,enterpriseId,accountId);",root=True);capture,fields=self.p4r_object_capture(component_open,'dialog')
        expected={'accountId':['accountId'],'tenantId':['tenantId'],'enterpriseId':['enterpriseId'],'context':['stateKey','(',')'],'mode':["'existing'"]}
        if any([t.value for t in fields.get(key,[])]!=value for key,value in expected.items()):raise Unsupported('Resource account binding form loses its original account/tenant/enterprise/context/existing mode')
        self.p4_execution_pattern(component_open,"busy=false;lastError='';draw();",root=True);draw=self.p4r_function(entry.get('draw'),component_open['parent']);slot=self.p4_flow_ui_slot(entry.get('layer_slot'));layers=[c for c in draw['model'].calls if draw['model'].enclosing_function(c.start)is draw['function']and c.path==('ui','openLayer')and len(c.args)>1]
        if slot['function'].symbol!='openLayer'or slot['index']!=1 or len(layers)!=1:raise Unsupported('Resource account binding has no original real string-layer sink')
        layer=layers[0];self.p4_execution_active_value(layer.args[1],'form');node,yes,no=self.p4r_boolean_branch(layer.args[1],'!current')
        if self.p4r_scalar(no,{})!='':raise Unsupported('Resource already-associated account branch is not its original read-only empty submit control')
        html,values=self.p4_rep_markup(layer.args[1],needle=(node[0].start,node[-1].end),conditional_branches=True);parser=TableHTMLContext(values);parser.feed(html);parser.close()
        if not any(key in parser.active and value and(value[0].start,value[-1].end)==(node[0].start,node[-1].end)for key,value in values.items()):raise Unsupported('Resource account binding submit control is not consumed in active original layer text')
        form_token,form=self.p4r_const(draw,'form',layer.start);condition,associated,unbound=self.p4_execution_ternary(form)
        if[x.value for x in condition]!=['current']:raise Unsupported('Resource account binding selection is not under its original unassociated account branch')
        selected_node,select,_=self.p4r_boolean_branch(unbound,"dialog.mode==='existing'",'AccountSeat.setTarget');context_html,context_values=self.p4_rep_markup(unbound,needle=(selected_node[0].start,selected_node[-1].end),conditional_branches=True);context_parser=TableHTMLContext(context_values);context_parser.feed(context_html);context_parser.close()
        if not any(k in context_parser.active and v and(v[0].start,v[-1].end)==(selected_node[0].start,selected_node[-1].end)for k,v in context_values.items()):raise Unsupported('Resource existing seat selector is not in the original active form context')
        selection_html,selection_values=self.p4_rep_markup(select,{'busy':False});selection_parser=TableHTMLContext(selection_values);selection_parser.feed(selection_html);selection_parser.close();setters=[]
        for ev,handler in selection_parser.handlers:
            if ev!='change':continue
            try:calls_=[c for c in calls(lex(handler))if c.path==('AccountSeat','setTarget')]
            except Unsupported:continue
            if len(calls_)==1 and len(calls_[0].args)==1 and[x.value for x in calls_[0].args[0]]==['this','.','value']:setters.append(calls_[0])
        setter=self.p4_flow_export(result.get('selection_setter'));sf,sparams,sbody=self.p4_flow_body(setter)
        if len(setters)!=1 or sparams!=['id']or setter['canonical_callee']!=('window','AccountSeat','setTarget')or[x.value for x in sbody]!=['if','(','dialog','&&','!','busy',')','dialog','.','identityId','=','id',';']:raise Unsupported('Resource actual existing-seat selection does not write its original dialog identity')
        save_event=result.get('save_event',{});self.p4_rep_quoted_control(yes,save_event,{'busy':False});submit=self.p4r_function(result.get('submit'),component_open['parent']);sp=self.p4_flow_export(save_event.get('owner_binding'))
        if sp['function']is not submit['function']or sp['canonical_callee']!=('window','AccountSeat','submit'):raise Unsupported('Resource actual account-binding user submit differs')
        self.p4r_guard(submit,'dialog.context!==stateKey()||scopeError(dialog.tenantId,dialog.enterpriseId,dialog.accountId)',submit['function'].body_end);self.p4_execution_pattern(submit,"const request=dialog;busy=true;lastError='';draw();",root=True);self.p4_execution_pattern(submit,"const result=request.mode==='new'?await create(request.input,{accountId:request.accountId,requestId:request.requestId}):await bind(request.identityId,request.accountId,{requestId:request.requestId});",root=True)
        out=self.p4r_function(result);bp=self.p4_flow_export(result.get('owner_binding'))
        if out['function']is not bp['function']or bp['canonical_callee']!=('window','AccountSeat','bind')or out['parent']is not submit['parent']:raise Unsupported('Resource existing-mode result does not resolve to the original bind implementation')
        self.p4_execution_pattern(out,'const context=stateKey(),agent=data.agents.find(a=>a.contactCenterIdentityId===identityId);',root=True);self.p4r_guard(out,'!agent',out['function'].body_end);locks=[c for c in out['model'].calls if out['model'].enclosing_function(c.start)is out['function']and c.path==('locked',)and len(c.args)==2 and[x.value for x in c.args[0]]==['identityId']]
        if len(locks)!=1:raise Unsupported('Resource account bind does not submit its actual same-identity lock callback')
        callback=self.p4_execution_arrow_step(out,locks[0].args[1]);lock_fn=self.p4r_function(result.get('lock_helper'),out['parent'])
        if lock_fn['symbol']!='locked'or lock_fn['params']!=['identityId','action']:raise Unsupported('Resource account bind uses a different original lock helper')
        self.no_shadow(out['model'],'locked',out['parent'],[locks[0].start]);self.p4_execution_pattern(callback,'write(saved);restore(saved);',root=True)
        cleanup=[c.args[0][0]for c in out['model'].calls if out['model'].enclosing_function(c.start)is out['function']and c.path==('active','delete')and len(c.args)==1 and[x.value for x in c.args[0]]==['identityId']]
        if len(cleanup)!=1:raise Unsupported('Resource account bind lacks its original same-identity cleanup')
        self.p4_no_write_contexts(out['model'],{'identityId','accountId'},out['function'].body_start,out['function'].body_end,cleanup);self.p4_execution_pattern(callback,'if(context!==stateKey())return',root=True)
        if[x.get('kind')for x in flow['flow_steps'][1:]]!=['membership_id_to_component_account','captured_existing_account_layer','actual_account_bind_result']:raise Unsupported('Resource account binding source edges are incomplete or unused')
        te=self.evidence(self.proof['source_chain']['terminal'].get('evidence'))
        if not te.contains(out['function']):raise Unsupported('Resource account binding changed the original bind observation anchor')
        return dict(entry_evidence=front['entry_evidence'],terminal_evidence=te,terminal_symbol=out['symbol'],result_evidence=out['evidence'],result_symbol=out['symbol'],followup_verified=False)

    def p4r_state_output_stable(self,info,path,value,name,target):
        # Only this original local string output is tracked. A control found
        # in an earlier assignment cannot borrow a later overwritten footer.
        initializers=[];allowed=[target]
        for ts in info['model'].lists:
            ms=matching(ts)
            for i,t in enumerate(ts[:-2]):
                if t.value not in {'const','let','var'}or i and ts[i-1].value=='('or info['model'].enclosing_function(t.start)is not info['function']:continue
                at=i+1
                while at<len(ts)and ts[at].value!=';':at=ms[at]+1 if ts[at].value in {'(','[','{'}else at+1
                for part in split_tokens(ts[i+1:at]):
                    if len(part)==3 and[x.value for x in part[:2]]==[name,'=']and self.p1_literal(part[2:])==''and part[0].start<target.start:initializers.append(part[0])
        if len(initializers)!=1:raise Unsupported('Resource stage control output lacks its unique original local empty string initializer')
        allowed+=initializers
        for ts in info['model'].lists:
            for i,t in enumerate(ts[:-1]):
                if t.value!=name or ts[i+1].value!='='or info['model'].enclosing_function(t.start)is not info['function']or any(t is token for token in allowed):continue
                guards=self.p4_rep_call_guards(info,Call((),ts,i,i,i,[]));excluded=False
                for condition,positive in guards:
                    condition=self.p3_unwrap(condition);v=[x.value for x in condition]
                    if len(condition)==5 and v[:3]==list(path)and v[3]in {'===','!=='}:
                        actual=self.p4_rep_literal(condition[4:]);truth=(type(actual)is type(value)and actual==value)==(v[3]=='===')
                        if truth!=positive:excluded=True
                if not excluded:raise Unsupported('Resource selected stage control output has a possible later or unproved competing write')
                allowed.append(t)
        self.p4_no_write_contexts(info['model'],{name},info['function'].body_start,info['function'].body_end,allowed)

    # Reporting consumes the same bounded-active-source-flow@1 envelope as the
    # other families.  These helpers parse source tokens/HTML only; they do not
    # evaluate JavaScript, invoke a prototype handler, or accept a candidate key.
    def p4_rep_function(self,spec):
        if not isinstance(spec,dict)or not IDENT.fullmatch(spec.get('symbol','')):raise Unsupported('Reporting flow needs an exact original named function')
        e=self.evidence(spec.get('evidence'));symbol=spec['symbol']
        fs=[f for f in e.model.functions if f.symbol==symbol and f.is_declaration and e.contains(f)]
        if len(fs)!=1:raise Unsupported('Reporting function is not one complete unique original declaration')
        f=fs[0];fn,params,body=self.p4_flow_function(e,symbol,f.parent)
        return dict(evidence=e,function=fn,parent=fn.parent,params=params,body=body,model=e.model,symbol=symbol)

    def p4_rep_unwrap(self,ts):
        while ts and ts[0].value=='('and matching(ts).get(0)==len(ts)-1:ts=ts[1:-1]
        return ts

    def p4_rep_split(self,ts,operator):
        out=[];first=0;i=0;ms=matching(ts)
        while i<len(ts):
            if ts[i].value in {'(','[','{'}:i=ms[i]+1;continue
            if ts[i].value==operator:out.append(ts[first:i]);first=i+1
            i+=1
        out.append(ts[first:]);return out

    def p4_rep_literal(self,ts):
        ts=self.p4_rep_unwrap(ts)
        if len(ts)!=1:raise Unsupported('Reporting fixed argument is not one original literal')
        t=ts[0]
        if t.kind=='string':return self.p4_quoted_html(t)
        if t.value in {'true','false','null'}:return {'true':True,'false':False,'null':None}[t.value]
        if t.kind=='number'and re.fullmatch(r'-?\d+(?:\.\d+)?',t.value):return float(t.value)if'.'in t.value else int(t.value)
        raise Unsupported('Reporting fixed argument has an unsupported literal type')

    def p4_rep_value(self,ts,bindings):
        ts=self.p4_rep_unwrap(ts)
        if len(ts)==1 and ts[0].kind=='id'and ts[0].value in bindings:return bindings[ts[0].value]
        return self.p4_rep_literal(ts)

    def p4_rep_condition(self,ts,bindings):
        # Only the scalar literal comparisons used by a proved route/view or
        # original function argument. Unknown runtime conditions are not true.
        ts=self.p4_rep_unwrap(ts)
        if len(ts)==1:
            if ts[0].value in {'true','false'}:return ts[0].value=='true'
            if ts[0].value in bindings and type(bindings[ts[0].value])is bool:return bindings[ts[0].value]
        if len(ts)==3 and ts[1].value in {'===','!=='}:
            try:a=self.p4_rep_value(ts[:1],bindings);b=self.p4_rep_value(ts[2:],bindings)
            except Unsupported:return None
            same=type(a)is type(b)and a==b
            return same if ts[1].value=='==='else not same
        return None

    def p4_rep_return(self,info,bindings=None):
        fn=info['function'];body=info['body'];ms=matching(body);found=[]
        for i,t in enumerate(body):
            if t.value!='return'or info['model'].enclosing_function(t.start)is not fn:continue
            if not self.p2_direct_statement(body,t.start):continue
            j=i+1
            while j<len(body)and body[j].value!=';':
                if body[j].value in {'(','[','{'}:j=ms[j]+1
                else:j+=1
            found.append(body[i+1:j])
        if len(found)!=1:raise Unsupported('Reporting HTML producer needs one direct original result return')
        # Literal true early branches are not conditional error guards.
        stop=found[0][0].start if found[0]else fn.end
        for i,t in enumerate(body[:-1]):
            if t.value!='if'or body[i+1].value!='('or t.start>=stop:continue
            close=ms[i+1]
            if self.p4_rep_condition(body[i+2:close],bindings or{})is True:raise Unsupported('Reporting selected result is hidden by a known prior branch')
        return found[0]

    def p4_rep_substitute(self,ts,bindings):
        out=[]
        for t in ts:
            if t.kind=='id'and t.value in bindings:
                value=bindings[t.value]
                if isinstance(value,str):out.append(Token('string',"'"+value.replace('\\','\\\\').replace("'","\\'")+"'",t.start,t.end))
                elif value is None:out.append(Token('id','null',t.start,t.end))
                elif type(value)is bool:out.append(Token('id','true'if value else'false',t.start,t.end))
                elif type(value)in {int,float}:out.append(Token('number',str(value),t.start,t.end))
                else:raise Unsupported('Reporting scalar binding cannot contain objects')
            else:out.append(t)
        return out

    def p4_rep_markup(self,ts,bindings=None,needle=None,strict=False,conditional_branches=False):
        # A finite string/template/concatenation projection. A needle marks the
        # *whole* actual producer call whose result must enter active HTML.
        bindings=bindings or{};expressions={};serial=[0]
        def marker(tokens):
            key='__REPORT_FLOW_'+str(serial[0])+'__';serial[0]+=1
            expressions[key]=self.p4_rep_substitute(tokens,bindings);return key
        def has(tokens):return bool(needle and tokens and tokens[0].start<=needle[0]and tokens[-1].end>=needle[1])
        def visit(tokens):
            tokens=self.p4_rep_unwrap(tokens)
            if not tokens:raise Unsupported('Reporting HTML expression is empty')
            if needle and(tokens[0].start,tokens[-1].end)==needle:return marker(tokens)
            parts=self.p4_rep_split(tokens,'+')
            if len(parts)>1:return ''.join(visit(p)for p in parts)
            # Match a complete top-level ternary, never regex-search a nested
            # unused string. A fixed selector must follow its actual operand.
            ms=matching(tokens);q=None;colon=None;depth=0;i=0
            while i<len(tokens):
                if tokens[i].value in {'(','[','{'}:i=ms[i]+1;continue
                if tokens[i].value=='?':
                    if q is None:q=i
                    depth+=1
                elif tokens[i].value==':'and q is not None:
                    depth-=1
                    if depth==0:colon=i;break
                i+=1
            if q is not None and colon is not None:
                truth=self.p4_rep_condition(tokens[:q],bindings)
                if truth is not None:return visit(tokens[q+1:colon]if truth else tokens[colon+1:])
                if has(tokens):
                    choices=[p for p in [tokens[q+1:colon],tokens[colon+1:]]if has(p)]
                    if conditional_branches and len(choices)==1:return visit(choices[0])
                    raise Unsupported('Reporting selected HTML branch needs a proved fixed scalar selector')
                if strict:raise Unsupported('Reporting control contains an unproved conditional argument')
                return marker(tokens)
            if len(tokens)==1:
                t=tokens[0]
                if t.kind=='string':return self.p4_quoted_html(t)
                if t.kind=='template':
                    text=[]
                    for kind,part in t.parts:
                        if kind=='text':text.append(part)
                        elif has(part):text.append(visit(part))
                        elif len(part)==1 and part[0].value in bindings:
                            value=bindings[part[0].value]
                            if not isinstance(value,str)or not re.fullmatch(r'[\w\u0080-\uffff -]*',value):raise Unsupported('Reporting interpolated fixed scalar is not safe literal text')
                            text.append(value)
                        else:text.append(marker(part))
                    return ''.join(text)
                if t.kind=='id'and t.value in bindings:
                    value=bindings[t.value]
                    if not isinstance(value,str)or not re.fullmatch(r'[\w\u0080-\uffff -]*',value):raise Unsupported('Reporting concatenated fixed scalar is not safe literal text')
                    return value
            if has(tokens):raise Unsupported('Reporting producer is not in the finite active return grammar')
            if strict:raise Unsupported('Reporting control has an unresolved dynamic string fragment')
            return marker(tokens)
        return visit(ts),expressions

    def p4_rep_active_call(self,info,call):
        expr=self.p4_rep_return(info);html,expressions=self.p4_rep_markup(expr,needle=(call.start,call.end))
        parser=TableHTMLContext(expressions);parser.feed(html);parser.close()
        if not any(key in parser.active and tokens and(tokens[0].start,tokens[-1].end)==(call.start,call.end)for key,tokens in expressions.items()):raise Unsupported('Reporting actual producer result does not reach active returned HTML')
        return expr

    def p4_rep_calls(self,info,path):
        target=normalized_callee(path)
        if not target:raise Unsupported('Reporting callee must be an exact static path')
        return[c for c in info['model'].calls if c.path==target and info['model'].enclosing_function(c.start)is info['function']]

    def p4_rep_scalar(self,info,name,bindings):
        if not IDENT.fullmatch(name):raise Unsupported('Reporting scalar alias must be a plain original identifier')
        matches=[]
        for ts in info['model'].lists:
            ms=matching(ts)
            for i,t in enumerate(ts[:-2]):
                if t.value!=name or not(info['function'].body_start<t.start<info['function'].body_end)or info['model'].enclosing_function(t.start)is not info['function']or ts[i+1].value!='=':continue
                j=i+2
                while j<len(ts)and ts[j].value not in {',',';'}:
                    if ts[j].value in {'(','[','{'}:j=ms[j]+1
                    else:j+=1
                if i==0 or ts[i-1].value not in {'const','let','var'}or not self.p2_direct_statement(info['body'],ts[i-1].start):raise Unsupported('Reporting scalar selector is not an actual direct source binding')
                value=self.p4_rep_condition(ts[i+2:j],bindings)
                if value is None:raise Unsupported('Reporting scalar selector is not an exact bound comparison')
                matches.append((t,value))
        if len(matches)!=1:raise Unsupported('Reporting scalar selector lacks one original binding')
        self.p4_no_write_contexts(info['model'],{name},info['function'].body_start,info['function'].body_end,allowed=(matches[0][0],))
        return matches[0][1]

    def p4_rep_dispatch(self,entry,route,page):
        spec=entry.get('dispatch',{});kind=spec.get('kind');render=self.p4_rep_function(entry.get('render'));delegate=self.p4_rep_function(spec.get('delegate'))
        if render['function']is not page['function']or render['model']is not delegate['model']or render['parent']is not delegate['parent']:raise Unsupported('Reporting render/delegate publication is disconnected')
        params=render['params'];option=spec.get('option_parameter');state=spec.get('state_variable');view=route['route_view']if route['route_view']is not None else spec.get('fixed_view')
        if params!=[option]or not IDENT.fullmatch(state or''):raise Unsupported('Reporting render selector parameters are not exact')
        body=render['body'];ms=matching(body);bindings={};selected=[]
        if kind=='view_literal_branches':
            reads=[state,'=',option,'&&',option,'.','view','||',state]
            if not any([x.value for x in body[i:i+9]]==reads for i in range(len(body)-8)):raise Unsupported('Reporting render does not select original route view')
            for i,t in enumerate(body[:-1]):
                if t.value!='if'or body[i+1].value!='(':continue
                close=ms[i+1];condition=body[i+2:close]
                if len(condition)!=3 or condition[0].value!=state or condition[1].value!='===':continue
                if self.p4_rep_literal(condition[2:])!=view:continue
                j=close+1
                if j>=len(body)or body[j].value!='return':raise Unsupported('Reporting route branch does not return its result')
                # Select by source interval and original immediate return.
                for c in self.p4_rep_calls(render,delegate['symbol']):
                    if c.start==body[j+1].start and c.index and c.tokens[c.index-1].value=='return':selected.append(c)
            if len(selected)!=1:raise Unsupported('Reporting fixed route/view lacks its exact returned delegate call')
            c=selected[0]
            if len(c.args)!=len(delegate['params']):raise Unsupported('Reporting delegate argument arity differs')
            bindings={p:self.p4_rep_literal(arg)for p,arg in zip(delegate['params'],c.args)}
        elif kind=='direct_option_forward':
            selected=[c for c in self.p4_rep_calls(render,delegate['symbol'])if c.index and c.tokens[c.index-1].value=='return']
            if len(selected)!=1 or len(selected[0].args)!=1 or[t.value for t in selected[0].args[0]]!=[option]or delegate['params']!=[option]:raise Unsupported('Reporting direct delegate does not forward the exact original options')
            if not any([x.value for x in body[i:i+3]]==[state,'=',repr(view)]or len(body[i:i+3])==3 and body[i].value==state and body[i+1].value=='='and self.p1_literal(body[i+2:i+3])==view for i in range(len(body)-2)):raise Unsupported('Reporting direct render does not preserve the selected view')
        else:raise Unsupported('Reporting render dispatch kind not yet supported by this finite profile')
        writes=[t for ts in render['model'].lists for i,t in enumerate(ts[:-1])if t.value==state and render['function'].body_start<t.start<render['function'].body_end and ts[i+1].value=='='and(i==0 or ts[i-1].value not in {'.','?.'})]
        if len(writes)!=1:raise Unsupported('Reporting route view is rewritten or shadowed during render')
        self.p4_no_write_contexts(render['model'],{state,option},render['function'].body_start,render['function'].body_end,allowed=writes)
        self.p4_no_write_contexts(delegate['model'],set(delegate['params']),delegate['function'].body_start,delegate['function'].body_end)
        self.p4_rep_call_guards(render,selected[0],{state:view})
        self.no_shadow(render['model'],delegate['symbol'],render['parent'],[selected[0].start])
        return render,delegate,bindings

    def p4_reporting_flow(self,flow):
        if not isinstance(flow,dict)or flow.get('version')!='bounded-active-source-flow@1'or flow.get('reachability')!='conditional_only'or flow.get('family')!='reporting':raise Unsupported('Unsupported reporting source-flow envelope')
        kind=flow.get('profile_kind');entry=flow.get('entry_binding',{});event=flow.get('event_binding',{});result=flow.get('result_binding',{});steps=flow.get('flow_steps')
        if not isinstance(steps,list)or not steps:raise Unsupported('Reporting flow needs its actual source consumption edges')
        route=self.p4_flow_route(entry.get('route'));page=self.p4_flow_export(entry.get('page_export'));target=self.p4_flow_export(event.get('publisher'))
        if normalized_callee(event.get('callee'))!=normalized_callee(self.proof['source_chain']['entry'].get('callee'))or event.get('event')!=self.proof['source_chain']['entry'].get('event'):raise Unsupported('Reporting event differs from its exact original chain entry')
        terminal=self.p4_rep_function(result.get('terminal'))
        if kind=='seat_event_query':return self.p4_rep_seat_event_flow(flow,route,page,target,terminal)
        if kind in {'import_batch_query','import_batch_detail'}:return self.p4_rep_import_flow(flow,route,page,target,terminal)
        if terminal['function']is not target['function']or terminal['model']is not target['model']:raise Unsupported('Reporting event publisher does not bind the original terminal')
        if kind in {'task_list_export','call_record_export'}:
            render,producer,bindings=self.p4_rep_dispatch(entry,route,page)
            if producer['model']is not terminal['model']or producer['parent']is not terminal['parent']:raise Unsupported('Reporting control and terminal are in different lexical owners')
            alias=event.get('ui_alias');ae=self.evidence(event.get('ui_alias_evidence'));self.p4_flow_ui_alias(ae.record,alias,producer['parent'],producer['function'].body_start+1)
            slot=event.get('ui_slot');contract=self.p4_flow_ui_slot(slot);helper=slot.get('helper_symbol');index=slot.get('argument_index')
            if contract['index']!=index:raise Unsupported('Reporting UI slot differs from actual library argument')
            for name in event.get('scalar_selectors',[]):bindings[name]=self.p4_rep_scalar(producer,name,bindings)
            candidates=[];e=self.evidence(event.get('evidence'))
            for call in self.p4_rep_calls(producer,alias+'.'+helper):
                if type(index)is not int or index<0 or index>=len(call.args)or not e.contains(call.tokens[call.index]):continue
                try:self.p4_rep_active_call(producer,call)
                except Unsupported:continue
                html,expressions=self.p4_rep_markup(call.args[index],bindings,strict=True);parser=TableHTMLContext(expressions);parser.feed(html);parser.close()
                for ev,h in parser.handlers:
                    if ev!=event['event']:continue
                    hs=lex(h);cs=calls(hs)
                    if len(cs)!=1 or cs[0].index!=0 or cs[0].end_index!=len(hs)-1 or cs[0].path!=normalized_callee(event['callee']):continue
                    values=[self.p4_rep_literal(a)for a in cs[0].args if a]
                    expected=event.get('literal_arguments',[])
                    if values!=expected:continue
                    if kind=='task_list_export'and(values!=list(bindings[p]for p in producer['params'])or len(values)!=1):continue
                    candidates.append(call)
            if len(candidates)!=1:raise Unsupported('Reporting concatenated control is not one actual active exact parameterized event')
            required={'render_to_producer','producer_to_UI_slot','UI_event_to_original_handler'}
            if {s.get('kind')for s in steps}!=required:raise Unsupported('Reporting source-edge declarations are incomplete or extraneous')
            if result.get('kind')!='handler':raise Unsupported('Reporting export must end at its complete original handler')
            return dict(entry_evidence=e,terminal_evidence=terminal['evidence'],terminal_symbol=terminal['symbol'],result_evidence=terminal['evidence'],result_symbol=terminal['symbol'])
        if kind=='workspace_task_control':return self.p4_rep_workspace_flow(flow,route,page,target,terminal)
        if kind=='report_drill':return self.p4_rep_report_drill(flow,route,page,target,terminal)
        raise Unsupported('Reporting source-flow profile requires its remaining finite original edges: '+str(kind))

    def p4_rep_direct_body_token(self,model,parent,token):
        if parent is None:return False
        for ts in model.lists:
            at=next((i for i,t in enumerate(ts)if t.start==parent.body_start and t.value=='{'),None)
            if at is None:continue
            ms=matching(ts);end=ms[at];i=at+1
            while i<end:
                if ts[i]is token:return True
                i=ms[i]+1 if ts[i].value in {'(','[','{'}else i+1
        return False

    def p4_rep_const_arrow(self,e,name,parent):
        found=[]
        for ts in e.model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts[:-4]):
                if [x.value for x in ts[i:i+3]]!=['const',name,'=']or e.model.scope(t.start)is not parent:continue
                j=i+3
                while j<len(ts)and ts[j].value!=';':j=ms[j]+1 if ts[j].value in {'(','[','{'}else j+1
                if e.start<=t.start and j<len(ts)and ts[j].end<=e.end:
                    if not self.p4_rep_direct_body_token(e.model,parent,t):raise Unsupported('Reporting immutable helper is not in its actual direct lexical body')
                    found.append((ts[i+1],ts[i+3:j]))
        if len(found)!=1:raise Unsupported('Reporting helper is not one exact same-scope immutable const arrow')
        self.p2_no_owner_writes(e.model,[(name,)],(found[0][0],))
        return found[0][1]

    def p4_rep_event_helper(self,spec,parent,use):
        e=self.evidence(spec.get('helper_evidence'));me=self.evidence(spec.get('mapper_evidence'));ae=self.evidence(spec.get('alias_evidence'));name=spec.get('helper_symbol');mapper=spec.get('mapper_symbol');escape=spec.get('escape_alias');ui=spec.get('ui_alias')
        if not all(IDENT.fullmatch(x or'')for x in [name,mapper,escape,ui])or len({name,mapper,escape,ui})!=4:raise Unsupported('Reporting helper aliases are not exact distinct identifiers')
        if len({id(x.model)for x in [e,me,ae]})!=1:raise Unsupported('Reporting fixed helper source scopes are disconnected')
        args=self.p4_rep_const_arrow(e,name,parent);vals=[t.value for t in args]
        if len(args)!=8 or vals[:6]!=['(','method',',','...','args',')']or vals[6]!='=>'or args[7].kind!='template':raise Unsupported('Reporting helper is not the bounded fixed-owner rest-argument template')
        parts=args[7].parts
        if [k for k,p in parts]!=['text','expression','text','expression','text']or[x.value for x in parts[1][1]]!=['method']or parts[2][1]!='('or parts[4][1]!=')'or not parts[0][1].endswith('.'):raise Unsupported('Reporting helper loses its fixed event receiver')
        owner=normalized_callee(parts[0][1][:-1]);expr=parts[3][1]
        if [x.value for x in expr]!=['args','.','map','(',mapper,')','.','join','(',"','",')']:raise Unsupported('Reporting helper arguments do not use the exact immutable mapper/join')
        m=self.p4_rep_const_arrow(me,mapper,parent);v=[t.value for t in m]
        if len(m)!=20 or v[:3]!=['value','=>',escape]or v[3:10]!=['(','JSON','.','stringify','(','String','(']or v[10:15]!=['value','==','null','?',"''"]or v[15:]!=[':','value',')',')']+[')']:raise Unsupported('Reporting event mapper is not String-null/JSON/HTML escaping')
        self.p4_flow_ui_alias(ae.record,ui,parent,use)
        # The only accepted bare use of the immutable mapper is this actual
        # args.map callback; the HTML escape alias is called, never forwarded.
        self.p2_const_alias(ae,escape,ui+'.escape',parent,use)
        self.no_shadow(e.model,'JSON',None,[m[4].start],include_parent=True);self.no_shadow(e.model,'String',None,[m[8].start],include_parent=True)
        self.no_shadow(e.model,name,parent,[use]);self.no_shadow(e.model,mapper,parent,[use])
        self.p2_escape(self.evidence(spec.get('escape_implementation_evidence')),self.evidence(spec.get('escape_export_evidence')))
        return dict(name=name,owner=owner,model=e.model,mapper=mapper)

    def p4_rep_helper_control(self,info,event,bindings,helper):
        html,expressions=self.p4_rep_markup(self.p4_rep_return(info),bindings);parser=TableHTMLContext(expressions);parser.feed(html);parser.close();hits=[]
        for kind,handler in parser.handlers:
            if kind!=event.get('event'):continue
            if handler not in expressions:continue
            ts=expressions[handler];cs=calls(ts)
            if len(cs)!=1 or cs[0].index!=0 or cs[0].end_index!=len(ts)-1 or cs[0].path!=(helper['name'],):continue
            values=[self.p4_rep_value(a,bindings)for a in cs[0].args]
            if values!=[normalized_callee(event.get('callee'))[-1]]+event.get('literal_arguments',[]):continue
            if helper['owner']!=normalized_callee(event['callee'])[:-1]:continue
            hits.append(cs[0])
        if len(hits)!=1:raise Unsupported('Reporting helper control does not bind one actual active exact event and fixed arguments')
        return hits[0]

    def p4_rep_literal_selector(self,info,bindings,spec):
        e=self.evidence(spec.get('evidence'));name=spec.get('symbol');ts=self.p4_rep_const_arrow(e,name,info['parent']);v=[t.value for t in ts]
        if v[:3]!=['(',')','=>']or len(ts)<10 or ts[3].value!='[':raise Unsupported('Reporting branch helper is not a fixed literal-membership selector')
        close=matching(ts)[3];items=[self.p4_rep_literal(x)for x in split_tokens(ts[4:close])]
        if v[close+1:]!=['.','includes','(',spec.get('state_variable'),')']or spec.get('state_variable')not in bindings:raise Unsupported('Reporting literal-membership selector lacks its bound original state')
        return name,bindings[spec['state_variable']]in items

    def p4_rep_array_call(self,info,spec,bindings):
        body=info['body'];ms=matching(body);name=spec.get('array_symbol');callee=spec.get('item_symbol');index=spec.get('argument_index');key=spec.get('literal');selected=None;assignments=[];i=0;truth=True;chosen=False
        # Exactly one mutually exclusive if/else-if/else assignment chain. It
        # yields original literal array operands; no loop/data evaluation.
        for at,t in enumerate(body):
            if t.value=='if'and at+1<len(body)and body[at+1].value=='(':
                cl=ms[at+1]
                if cl+3<len(body)and[x.value for x in body[cl+1:cl+4]]==[name,'=','[']:i=at;break
        else:raise Unsupported('Reporting metric array lacks its original conditional assignment chain')
        starts=[]
        while i<len(body):
            if body[i].value=='if':
                cl=ms[i+1];condition=body[i+2:cl];value=self.p4_rep_condition(condition,bindings)
                if value is None and len(condition)==3 and condition[0].value in bindings and condition[1].value=='('and condition[2].value==')':value=bindings[condition[0].value]
                if type(value)is not bool:raise Unsupported('Reporting metric array branch is not fixed by the actual route view')
                at=cl+1;take=not chosen and value
            else:at=i;take=not chosen
            if[x.value for x in body[at:at+3]]!=[name,'=','[']:raise Unsupported('Reporting metric branch does not assign its exact literal array')
            end=ms[at+2];assignments.append(body[at]);starts.append(body[at].start)
            if take:selected=body[at+3:end];chosen=True
            i=end+1
            if i>=len(body)or body[i].value!=';':raise Unsupported('Reporting array assignment has extra source expressions')
            i+=1
            if i>=len(body)or body[i].value!='else':break
            i+=1
        if selected is None:raise Unsupported('Reporting route selects no original metric array')
        self.p4_no_write_contexts(info['model'],{name},info['function'].body_start,info['function'].body_end,allowed=assignments)
        direct=[]
        for part in split_tokens(selected):
            cs=calls(part)
            if not cs or cs[0].index!=0 or cs[0].path!=(callee,)or cs[0].end_index!=len(part)-1:raise Unsupported('Reporting metric array contains an unsupported item producer')
            c=cs[0]
            if type(index)is int and index<len(c.args):
                try:value=self.p4_rep_value(c.args[index],bindings)
                except Unsupported:continue
                if type(value)is type(key)and value==key:direct.append(c)
        if len(direct)!=1:raise Unsupported('Reporting selected view does not contain its exact original metric item')
        joins=[c for c in self.p4_rep_calls(info,name+'.join')if len(c.args)==1 and self.p4_rep_literal(c.args[0])=='']
        if len(joins)!=1:raise Unsupported('Reporting metric array does not have one empty-separator result join')
        self.p4_rep_active_call(info,joins[0]);return direct[0]

    def p4_rep_report_drill(self,flow,route,page,target,terminal):
        entry=flow['entry_binding'];event=flow['event_binding'];result=flow['result_binding'];render=self.p4_rep_function(entry.get('render'));cards=self.p4_rep_function(entry.get('cards'));card=self.p4_rep_function(entry.get('card'))
        if render['function']is not page['function']or len({id(x['model'])for x in [render,cards,card,terminal]})!=1 or len({id(x['parent'])for x in [render,cards,card,terminal]})!=1:raise Unsupported('Reporting drill sources are in different lexical owners')
        view=route['route_view'];state=entry.get('state_variable');defs=entry.get('definition_symbol');opt=render['params'][0]if len(render['params'])==1 else None
        pattern=[state,'=',defs,'[',opt,'?.','view',']','?',opt,'.','view',':',"'overview'"]
        if not any([x.value for x in render['body'][i:i+len(pattern)]]==pattern for i in range(len(render['body']))):raise Unsupported('Reporting render lacks its exact bounded definitions/view route selector')
        de=self.evidence(entry.get('definitions_evidence'));found=[]
        for ts in de.model.lists:
            for i,t in enumerate(ts[:-4]):
                if[x.value for x in ts[i:i+3]]!=['const',defs,'=']or ts[i+3].value!='{':continue
                end=matching(ts)[i+3]
                if not(de.start<=t.start and ts[end].end<=de.end):continue
                for prop in split_tokens(ts[i+4:end]):
                    if len(prop)>4 and prop[0].value==view and[x.value for x in prop[1:3]]==[':','[']:
                        vals=[self.p4_rep_literal(x)for x in split_tokens(prop[3:-1])]
                        if len(vals)==4 and vals[1]==route['route_key']:found.append(prop)
        if len(found)!=1:raise Unsupported('Reporting view definition does not bind the actual route key')
        rc=self.p4_rep_calls(render,cards['symbol'])
        if len(rc)!=1 or len(rc[0].args)!=1 or[x.value for x in rc[0].args[0]]!=['m']:raise Unsupported('Reporting rendered metrics do not consume the exact scoped model')
        self.p4_rep_active_call(render,rc[0]);bindings={state:view}
        for selector in entry.get('literal_selectors',[]):
            name,value=self.p4_rep_literal_selector(cards,bindings,selector);bindings[name]=value
        selected=self.p4_rep_array_call(cards,event.get('array_selection',{}),bindings)
        if len(selected.args)>len(card['params'])or len(selected.args)<=event['array_selection']['argument_index']:raise Unsupported('Reporting card argument/parameter arity differs')
        # Only identity/event selector parameters are evaluated; labels/counts
        # remain opaque and cannot create another event or row identity.
        cb={card['params'][i]:self.p4_rep_value(arg,bindings)for i,arg in enumerate(selected.args)if i==event['array_selection']['argument_index']}
        helper=self.p4_rep_event_helper(event.get('helper'),card['parent'],card['function'].body_start+1);self.p4_rep_helper_control(card,event,cb,helper)
        show=self.p4_rep_function(result.get('display'));getter=self.p4_rep_function(result.get('rows'))
        if any(x['model']is not terminal['model']or x['parent']is not terminal['parent']for x in [show,getter]):raise Unsupported('Reporting drill result owner is disconnected')
        if terminal['params']!=['kind','key']:raise Unsupported('Reporting drill handler parameter slots differ')
        pattern=['drillState','=','{','kind',',','key',':','String','(','key',')',',','page',':','1','}']
        if not any([x.value for x in terminal['body'][i:i+len(pattern)]]==pattern for i in range(len(terminal['body']))):raise Unsupported('Reporting drill does not capture the exact kind/key result state')
        sc=self.p4_rep_calls(terminal,show['symbol'])
        if len(sc)!=1 or any(sc[0].args) or not self.p2_direct_statement(terminal['body'],sc[0].start):raise Unsupported('Reporting drill does not directly open its exact result display')
        gc=self.p4_rep_calls(show,getter['symbol'])
        if len(gc)!=1 or any(gc[0].args) or gc[0].index<3 or[x.value for x in gc[0].tokens[gc[0].index-3:gc[0].index]]!=['const','result','=']:raise Unsupported('Reporting display does not consume its actual drill rows')
        alias=event['helper']['ui_alias'];self.p4_flow_ui_alias(self.evidence(event['helper']['alias_evidence']).record,alias,show['parent'],show['function'].body_start+1)
        contract=self.p4_flow_ui_slot(result.get('ui_slot'));lc=self.p4_rep_calls(show,alias+'.openLayer')
        if len(lc)!=1 or len(lc[0].args)<2 or self.p4_rep_literal(lc[0].args[0])!='report-detail'or not self.p2_direct_statement(show['body'],lc[0].start):raise Unsupported('Reporting drill result lacks its exact real layer insertion')
        tc=self.p4_rep_calls(show,alias+'.table')
        if len(tc)!=1 or len(tc[0].args)<2 or tc[0].start<lc[0].args[1][0].start or tc[0].end>lc[0].args[1][-1].end or[x.value for x in tc[0].args[1]][:4]!=['result','.','rows','.']:raise Unsupported('Reporting detail layer does not contain the actual scoped result rows table')
        html,expressions=self.p4_rep_markup(lc[0].args[1],needle=(tc[0].start,tc[0].end));parser=TableHTMLContext(expressions);parser.feed(html);parser.close()
        if not any(k in parser.active and t and(t[0].start,t[-1].end)==(tc[0].start,tc[0].end)for k,t in expressions.items()):raise Unsupported('Reporting result table is hidden in an inert layer context')
        self.p1_table_contract(self.evidence(result.get('table_implementation_evidence')),self.evidence(result.get('table_export_evidence')),result.get('table_key'))
        if {s.get('kind')for s in flow['flow_steps']}!={'route_view_to_metrics','metrics_array_to_card','card_event_to_original_drill','drill_to_result_layer'}:raise Unsupported('Reporting drill source-edge declarations are incomplete')
        return dict(entry_evidence=self.evidence(event.get('evidence')),terminal_evidence=terminal['evidence'],terminal_symbol=terminal['symbol'],result_evidence=show['evidence'],result_symbol=show['symbol'])

    def p4_rep_quoted_control(self,expr,event,bindings=None,identity=None):
        html,expressions=self.p4_rep_markup(expr,bindings);p=TableHTMLContext(expressions);p.feed(html);p.close();hits=[]
        for kind,h in p.handlers:
            if kind!=event.get('event'):continue
            ts=lex(h);cs=calls(ts)
            if len(cs)!=1 or cs[0].index!=0 or cs[0].end_index!=len(ts)-1 or cs[0].path!=normalized_callee(event.get('callee')):continue
            if identity is None:
                try:values=[self.p4_rep_literal(x)for x in cs[0].args if x]
                except Unsupported:continue
                if values==event.get('literal_arguments',[]):hits.append(cs[0])
            else:
                if len(cs[0].args)!=1+len(event.get('literal_arguments',[])):continue
                try:tail=[self.p4_rep_literal(a)for a in cs[0].args[1:]]
                except Unsupported:continue
                if tail!=event.get('literal_arguments',[]):continue
                arg=cs[0].args[0]
                if len(arg)!=1:continue
                marker=literal(arg[0])if arg[0].kind=='string'else arg[0].value;actual=expressions.get(marker)
                if actual is None:continue
                if identity.get('mapper')is None and[x.value for x in actual]in identity['accepted_arguments']:hits.append(cs[0]);continue
                ac=calls(actual)
                if len(ac)==1 and ac[0].index==0 and ac[0].end_index==len(actual)-1 and ac[0].path==(identity['mapper'],)and len(ac[0].args)==1 and[x.value for x in ac[0].args[0]]in identity['accepted_arguments']:hits.append(cs[0])
        if len(hits)!=1:raise Unsupported('Reporting returned control lacks its exact active event/literal or row identity binding')
        return hits[0]

    def p4_rep_table_control(self,info,event,spec,bindings=None):
        alias=spec['ui_alias'];self.p4_flow_ui_alias(spec['ui_alias_evidence'],alias,info['parent'],info['function'].body_start+1)
        key=spec['key'];self.p1_table_contract(self.evidence(spec['table_implementation_evidence']),self.evidence(spec['table_export_evidence']),key);hits=[];e=self.evidence(event['evidence'])
        for call in self.p4_rep_calls(info,alias+'.table'):
            if len(call.args)<2:continue
            cols=call.args[0]
            if not cols or cols[0].value!='['or matching(cols).get(0)!=len(cols)-1:continue
            self.p4_rep_active_call(info,call);self.p2_table_rows(info['model'],info['function'],call,alias)
            for part in split_tokens(cols[1:-1]):
                try:props=self.p1_props(part)
                except Unsupported:continue
                if self.p1_literal(props.get('key',[]))!=key:continue
                value=props.get('render',[])
                if not value:continue
                vm=matching(value);params=[];expr=None;renderer=None
                if value[0].value=='(':
                    close=vm[0];ps=split_tokens(value[1:close]);params=[x[0].value for x in ps if len(x)==1 and x[0].kind=='id']
                    if len(params)!=len(ps)or value[close+1].value!='=>':continue
                    expr=value[close+2:]
                elif value[0].value=='function'and value[1].value=='(':
                    close=vm[1];ps=split_tokens(value[2:close]);params=[x[0].value for x in ps if len(x)==1 and x[0].kind=='id']
                    if len(params)!=len(ps):continue
                    fs=[f for f in info['model'].functions if f.start==value[0].start]
                    if len(fs)!=1:continue
                    renderer=dict(function=fs[0],params=params,body=value[close+2:-1],model=info['model'],parent=info['function']);expr=self.p4_rep_return(renderer,bindings)
                if expr is None or len(params)<2 or not e.contains(expr[0])or not e.contains(expr[-1]):continue
                identity=dict(mapper=spec['mapper'],accepted_arguments=[[params[0]],[params[1],'.',key]])
                try:self.p4_rep_quoted_control(expr,event,bindings,identity)
                except Unsupported:continue
                self.p4_no_write_contexts(info['model'],set(params),value[0].start,value[-1].end)
                hits.append(dict(call=call,params=params,expression=expr,renderer=renderer))
        if len(hits)!=1:raise Unsupported('Reporting table has no unique active original key/row control')
        return hits[0]

    def p4_rep_value_mapper(self,spec,parent,use):
        e=self.evidence(spec['evidence']);name=spec['symbol'];escape=spec['escape_alias'];ts=self.p4_rep_const_arrow(e,name,parent);v=[t.value for t in ts]
        expected=['value','=>',escape,'(','JSON','.','stringify','(','String','(','value','||',"''",')',')',')']
        if v!=expected:raise Unsupported('Reporting row argument mapper is not the original bounded JSON/HTML string mapper')
        ae=self.evidence(spec['alias_evidence']);self.p4_flow_ui_alias(ae.record,spec['ui_alias'],parent,use);self.p2_const_alias(ae,escape,spec['ui_alias']+'.escape',parent,use);self.p2_escape(self.evidence(spec['escape_implementation_evidence']),self.evidence(spec['escape_export_evidence']))
        self.no_shadow(e.model,'JSON',None,[ts[4].start],include_parent=True);self.no_shadow(e.model,'String',None,[ts[8].start],include_parent=True)
        return name

    def p4_rep_import_flow(self,flow,route,page,target,terminal):
        entry=flow['entry_binding'];event=flow['event_binding'];result=flow['result_binding'];render=self.p4_rep_function(entry['render'])
        if render['function']is not page['function']or render['model']is not target['model']or render['parent']is not target['parent']:raise Unsupported('Import list/query publication is disconnected from its actual render')
        e=self.evidence(event['evidence']);kind=flow['profile_kind'];routekey=route['route_key']
        if routekey!='customer-tasks'or render['params']!=['options']:raise Unsupported('Import flow lacks its original list route/options')
        if kind=='import_batch_query':
            self.p4_rep_quoted_control(self.p4_rep_return(render),event)
            fn,params,body=self.p4_flow_body(target)
            nav=[c for c in target['model'].calls if c.path==('navigateTo',)and target['model'].enclosing_function(c.start)is fn]
            if params or len(nav)!=1 or len(nav[0].args)!=2 or self.p4_rep_literal(nav[0].args[0])!=routekey or[x.value for x in nav[0].args[1]]!=['{','batchId',':','selectedBatch','}']or not self.p2_direct_statement(body,nav[0].start):raise Unsupported('Import query does not navigate the original route with its exact selected batch context')
            if terminal['function']is not render['function']:raise Unsupported('Import query original result renderer differs')
        elif kind=='import_batch_detail':
            spec=event['table'];self.p4_rep_value_mapper(event['identity_mapper'],render['parent'],render['function'].body_start+1);self.p4_rep_table_control(render,event,spec)
            openfn=self.p4_rep_function(event['opening']);
            if openfn['function']is not target['function']or openfn['params']!=['batchId']:raise Unsupported('Import detail event does not publish its exact original opener')
            oc=self.p4_rep_calls(openfn,'RouteRuntime.openSecondary')
            if len(oc)!=1 or len(oc[0].args)!=2 or self.p4_rep_literal(oc[0].args[0])!=routekey or[x.value for x in oc[0].args[1]]!=['{','batchId',':','target',',','refreshOnClose',':','true','}']:raise Unsupported('Import opener does not send its actual batch identity to the internal route')
            pat=['const','target','=','batchId','||',"''"]
            if not any([x.value for x in openfn['body'][i:i+len(pat)]]==pat for i in range(len(openfn['body']))):raise Unsupported('Import opener batch identity is not the original immutable selection')
            dc=self.p4_rep_calls(render,terminal['symbol'])
            if len(dc)!=1 or len(dc[0].args)!=1 or[x.value for x in dc[0].args[0]]!=['b']or not dc[0].index or dc[0].tokens[dc[0].index-1].value!='return':raise Unsupported('Import selected batch is not returned through its original detail renderer')
            pats=[['const','b','=','displayBatch','(','selectedBatch',')'],['selectedBatch','=','requested']]
            if any(not any([x.value for x in render['body'][i:i+len(pat)]]==pat for i in range(len(render['body'])))for pat in pats):raise Unsupported('Import route selection does not resolve the same original batch object')
            if terminal['params']!=['b']or not self.p4_rep_calls(terminal,'ui.table'):raise Unsupported('Import detail does not render its original batch customers')
        else:raise Unsupported('Unsupported import source-flow kind')
        if {s.get('kind')for s in flow['flow_steps']}!={'original_list_control','event_to_route_with_batch_context','route_to_original_result'}:raise Unsupported('Import source-edge declarations are incomplete')
        return dict(entry_evidence=e,terminal_evidence=terminal['evidence'],terminal_symbol=terminal['symbol'],result_evidence=terminal['evidence'],result_symbol=terminal['symbol'])

    def p4_rep_statement_end(self,ts,at):
        ms=matching(ts)
        if at>=len(ts):raise Unsupported('Reporting guard has no actual statement')
        if ts[at].value=='{':return ms[at]+1
        if ts[at].value=='if'and at+1<len(ts)and ts[at+1].value=='(':
            end=self.p4_rep_statement_end(ts,ms[at+1]+1)
            return self.p4_rep_statement_end(ts,end+1)if end<len(ts)and ts[end].value=='else'else end
        i=at
        while i<len(ts):
            if ts[i].value==';':return i+1
            i=ms[i]+1 if ts[i].value in {'(','[','{'}else i+1
        return i

    def p4_rep_call_guards(self,info,call,bindings=None):
        bindings=bindings or{};body=info['body'];ms=matching(body);guards=[]
        for i,t in enumerate(body[:-1]):
            if t.value!='if'or body[i+1].value!='('or info['model'].enclosing_function(t.start)is not info['function']:continue
            close=ms[i+1];start=close+1;end=self.p4_rep_statement_end(body,start);selected=body[start].start<=call.start<body[end-1].end
            opposite=False
            if not selected and end<len(body)and body[end].value=='else':
                alt=end+1;ae=self.p4_rep_statement_end(body,alt);selected=body[alt].start<=call.start<body[ae-1].end;opposite=selected
            if not selected:continue
            condition=body[i+2:close];truth=self.p4_rep_condition(condition,bindings)
            if truth is not None and truth==opposite:raise Unsupported('Reporting actual control is excluded by its original static guard')
            guards.append((condition,not opposite))
        equals={};excludes={}
        for cond,positive in guards:
            if len(cond)==5 and cond[0].kind=='id'and cond[1].value=='.'and cond[2].kind=='id'and cond[3].value in {'===','!=='}:
                value=self.p4_rep_literal(cond[4:]);path=(cond[0].value,cond[2].value);is_eq=positive==(cond[3].value=='===')
                if is_eq:
                    if path in equals and(type(equals[path])is not type(value)or equals[path]!=value)or value in excludes.get(path,[]):raise Unsupported('Reporting control has contradictory original row guards')
                    equals[path]=value
                else:
                    if path in equals and type(equals[path])is type(value)and equals[path]==value:raise Unsupported('Reporting control has contradictory negative row guards')
                    excludes.setdefault(path,[]).append(value)
        return guards

    def p4_rep_push_event(self,info,event,spec):
        array=spec['array_symbol'];row=info['params'][0]if len(info['params'])==1 else None;body=info['body'];allowed=[]
        if not row:raise Unsupported('Reporting action-array needs its one original object parameter')
        decl=[body[i+1]for i in range(len(body)-5)if[x.value for x in body[i:i+6]]==['const',array,'=','[',']',';']]
        if len(decl)!=1:raise Unsupported('Reporting action array lacks one immutable empty initializer')
        di=next(i for i,t in enumerate(body)if t is decl[0])
        if not self.p2_direct_statement(body,body[di-1].start):raise Unsupported('Reporting action array initializer is outside the actual function-body path')
        self.p4_no_write_contexts(info['model'],{array,row},info['function'].body_start,info['function'].body_end,allowed=decl)
        self.p2_state_mutators(info['model'],{array,row},info['function'].body_start,info['function'].body_end,label='Reporting action identity/array')
        for ts in info['model'].lists:
            for i,t in enumerate(ts):
                if t.value!=row or not(info['function'].body_start<t.start<info['function'].body_end)or i and ts[i-1].value in {'.','?.'}:continue
                parsed=path_at(ts,i,False,partial=True)
                if parsed and parsed[0]==(row,):
                    # Existing predicate/markup peers remain conditional source
                    # guards. Copies/collections/whole-object returns are not
                    # original identity-preserving uses and stay pending.
                    if not(i and i+1<len(ts)and ts[i-1].value=='('and ts[i+1].value==')'and i>=2 and(ts[i-2].kind=='id'or ts[i-2].value=='?.')):raise Unsupported('Reporting action identity has an unproved bare copy/collection/forward value')
        joins=self.p4_rep_calls(info,array+'.join')
        if len(joins)!=1 or len(joins[0].args)!=1 or self.p4_rep_literal(joins[0].args[0])!='':raise Unsupported('Reporting action-array result is not its original empty-separator join')
        ret=self.p4_rep_return(info)
        if not ret or(ret[0].start,ret[-1].end)!=(joins[0].start,joins[0].end):raise Unsupported('Reporting action array does not return its actual built controls')
        e=self.evidence(event['evidence']);hits=[]
        for push in self.p4_rep_calls(info,array+'.push'):
            if len(push.args)!=1:raise Unsupported('Reporting action array has unsupported multi-item push')
            for ts in iter_lists(push.args[0]):
                for leaf in ts:
                    if leaf.kind not in {'template','string'}or not e.contains(leaf):continue
                    try:
                        html,expr=self.p4_rep_markup(push.args[0],needle=(leaf.start,leaf.end),conditional_branches=True);p=TableHTMLContext(expr);p.feed(html);p.close()
                        if not any(k in p.active and x and(x[0].start,x[-1].end)==(leaf.start,leaf.end)for k,x in expr.items()):continue
                        self.p4_rep_quoted_control([leaf],event,identity=dict(mapper=None,accepted_arguments=[[row,'.',spec['identity_field']]]));self.p4_rep_call_guards(info,push)
                    except Unsupported:continue
                    hits.append(push)
        if len(hits)!=1:raise Unsupported('Reporting built action array has no unique reachable exact original object/action event')
        # Unknown array mutations/copies are never additional accepted syntax.
        if any(c.path[:1]==(array,)and c.path[1:]not in {('push',),('join',)}for c in info['model'].calls if info['function'].body_start<c.start<info['function'].body_end):raise Unsupported('Reporting action-array has an unproved mutation or consumer')
        return hits[0]

    def p4_rep_task_rows(self,info,table,bindings):
        if len(table.args)<2 or[x.value for x in table.args[1]]!=['pageRows']:raise Unsupported('Task entry table does not use its actual preserving pageRows')
        body=info['body'];ms=matching(body);rows=self.p4_rep_calls(info,'taskRows');clones=[]
        for name in ['rows','pageRows']:
            declarations=[i for i in range(len(body)-2)if[x.value for x in body[i:i+3]]==['var',name,'=']]
            if len(declarations)!=1 or not self.p2_direct_statement(body,body[declarations[0]].start):raise Unsupported('Task row/page binding is outside the actual direct function-body path')
        if len(rows)!=1 or len(rows[0].args)!=1 or self.p4_rep_value(rows[0].args[0],bindings)not in {'预外呼','IVR 外呼'}:raise Unsupported('Task entry rows do not preserve the route-bound task type')
        for c in info['model'].calls:
            if c.path!=('Object','assign')or len(c.args)!=2 or not(info['function'].body_start<c.start<info['function'].body_end):continue
            if[x.value for x in c.args[1]]!=['row']:continue
            props=self.p1_props(c.args[0])
            if set(props)!={'orderNo'}:raise Unsupported('Task entry clone overwrites a non-display identity field')
            if any(t.kind not in {'id','number'}and t.value not in {'(',')','+','-','*','/','.'}for t in props['orderNo'])or any(t.kind=='id'and t.value not in {'state','page','pageSize','index'}for t in props['orderNo']):raise Unsupported('Task row display numbering contains an unproved computation')
            f=info['model'].enclosing_function(c.start)
            if f is None or f.parent is not info['function']:continue
            tokens=next(ts for ts in info['model'].lists if any(t.start==f.start for t in ts));at=next(i for i,t in enumerate(tokens)if t.start==f.start);fm=matching(tokens);pc=at+1 if tokens[at].value=='function'and tokens[at+1].value=='('else at+2
            if pc>=len(tokens)or tokens[pc].value!='(':raise Unsupported('Task preserving row clone lacks its actual plain function parameters')
            args=split_tokens(tokens[pc+1:fm[pc]]);ps=[a[0].value for a in args if len(a)==1 and a[0].kind=='id'];bb=tokens[fm[pc]+2:fm[fm[pc]+1]]
            if ps!=['row','index']or[x.value for x in bb[:3]]!=['var','displayRow','=']or bb[3].start!=c.start:continue
            tail=[t.value for t in bb if t.start>=c.end]
            if tail!=[';','return','displayRow',';']:raise Unsupported('Task row clone is overwritten or not returned unchanged')
            clones.append(c)
        if len(clones)!=1:raise Unsupported('Task entry page row map lacks its exact identity-preserving clone')
        self.p4_no_write_contexts(info['model'],{'pageRows','rows'},info['function'].body_start,info['function'].body_end,allowed=[t for t in body if t.value in {'pageRows','rows'}and next((i for i,x in enumerate(body)if x is t),0)>0 and body[next(i for i,x in enumerate(body)if x is t)-1].value=='var'])
        slices=self.p4_rep_calls(info,'rows.slice')
        if len(slices)!=1 or len(slices[0].args)!=2 or[x.value for x in slices[0].args[0]]!=['(','state','.','page','-','1',')','*','pageSize']or[x.value for x in slices[0].args[1]]!=['state','.','page','*','pageSize']:raise Unsupported('Task entry rows have an empty or unproved pagination slice')
        self.no_shadow(info['model'],'Object',None,[clones[0].start],include_parent=True)
        expected=['var','pageRows','=','rows','.','slice','(']
        if not any([x.value for x in body[i:i+len(expected)]]==expected for i in range(len(body))):raise Unsupported('Task table rows are not the original finite pagination/map')
        return True

    def p4_rep_workspace_flow(self,flow,route,page,target,terminal):
        entry=flow['entry_binding'];event=flow['event_binding'];result=flow['result_binding'];initial=entry['initial_list'];lr=self.p4_flow_route(initial['route']);lp=self.p4_flow_export(initial['page_export']);render,producer,bindings=self.p4_rep_dispatch(initial,lr,lp)
        if list(bindings.values())!=['IVR 外呼']:raise Unsupported('Task control entry is not its original IVR task-list type')
        for name in initial['table_event'].get('scalar_selectors',[]):bindings[name]=self.p4_rep_scalar(producer,name,bindings)
        s=initial['table_event'];alias=s['table']['ui_alias'];self.p4_flow_ui_alias(s['table']['ui_alias_evidence'],alias,producer['parent'],producer['function'].body_start+1);self.p1_table_contract(self.evidence(s['table']['table_implementation_evidence']),self.evidence(s['table']['table_export_evidence']),'taskId')
        # Task list uses an original identity-preserving var pagination/map.
        tables=self.p4_rep_calls(producer,alias+'.table')
        if len(tables)!=1:raise Unsupported('Original task list lacks one actual task table')
        table=tables[0];self.p4_rep_active_call(producer,table);self.p4_rep_task_rows(producer,table,bindings)
        cols=table.args[0];objects=[self.p1_props(x)for x in split_tokens(cols[1:-1])];matches=[];ee=self.evidence(s['evidence'])
        for props in objects:
            if self.p1_literal(props.get('key',[]))!='taskId':continue
            value=props.get('render',[])
            if len(value)<5 or value[0].value!='function'or value[1].value!='(':continue
            close=matching(value)[1];ps=[x[0].value for x in split_tokens(value[2:close])if len(x)==1];fs=[f for f in producer['model'].functions if f.start==value[0].start]
            if len(ps)!=2 or len(fs)!=1:continue
            ri=dict(function=fs[0],params=ps,body=value[close+2:-1],model=producer['model'],parent=producer['function']);expr=self.p4_rep_return(ri,bindings)
            if not ee.contains(expr[0])or not ee.contains(expr[-1]):continue
            try:self.p4_rep_quoted_control(expr,s,bindings,dict(mapper='esc',accepted_arguments=[[ps[0]],[ps[1],'.','taskId']]))
            except Unsupported:continue
            matches.append(ri)
        if len(matches)!=1:raise Unsupported('Original task list entry does not bind its exact taskId row')
        op=self.p4_flow_export(initial['opening_publication']);wf=self.p4_rep_function(initial['opening']);forward=self.p4_rep_function(initial['forwarding']);pub=self.p4_flow_export(initial['forwarding_publication'])
        if op['function']is not wf['function']or pub['function']is not forward['function']or wf['params']!=['id']or forward['params'][:1]!=['taskId']:raise Unsupported('Task opening/forwarding publications or identity parameters differ')
        wc=self.p4_rep_calls(wf,'CloudTaskWorkspace.openTask')
        if len(wc)!=1 or len(wc[0].args)!=1 or[x.value for x in wc[0].args[0]]!=['id']or not wc[0].index or wc[0].tokens[wc[0].index-1].value!='return':raise Unsupported('Task-list opener does not return the actual same-ID workspace forwarding')
        nc=self.p4_rep_calls(forward,'RouteRuntime.openSecondary')
        if len(nc)!=1 or len(nc[0].args)!=2 or self.p4_rep_literal(nc[0].args[0])!=route['route_key']or[x.value for x in nc[0].args[1]]!=['options']:raise Unsupported('Workspace opener does not send the selected task to its actual internal route')
        pattern=['const','options','=','{','taskId',',','tab',':']
        if not any([x.value for x in forward['body'][i:i+len(pattern)]]==pattern for i in range(len(forward['body']))):raise Unsupported('Workspace opening options do not preserve original taskId')
        center=self.p4_rep_function(entry['render']);actions=self.p4_rep_function(entry['actions']);context=self.p4_rep_function(entry['context'])
        if center['function']is not page['function']or actions['params']!=['row']or any(x['model']is not terminal['model']or x['parent']is not terminal['parent']for x in [center,actions,context,forward]):raise Unsupported('Workspace original route/render/actions are disconnected')
        patterns=[['const','context','=','readCenterContext','(','options',')'],['const','row','=','taskById','(','context','.','taskId',')']]
        if any(not any([x.value for x in center['body'][i:i+len(pat)]]==pat for i in range(len(center['body'])))for pat in patterns):raise Unsupported('Workspace render does not resolve its same original task object')
        if not any([x.value for x in context['body'][i:i+7]]==['const','taskId','=','options','?.','taskId','||']for i in range(len(context['body']))):raise Unsupported('Workspace context does not read actual route taskId')
        alias=event['ui_alias'];self.p4_flow_ui_alias(event['ui_alias_evidence'],alias,center['parent'],center['function'].body_start+1);self.p4_flow_ui_slot(entry['actions_slot']);hc=self.p4_rep_calls(center,alias+'.pageHeader');ac=self.p4_rep_calls(center,actions['symbol'])
        if len(ac)!=1 or len(ac[0].args)!=1 or[x.value for x in ac[0].args[0]]!=['row']:raise Unsupported('Workspace header actions lose the selected object')
        hs=[h for h in hc if len(h.args)>2 and h.args[2][0].start<=ac[0].start and ac[0].end<=h.args[2][-1].end]
        if len(hs)!=1:raise Unsupported('Workspace actions are not in the actual pageHeader action slot')
        self.p4_rep_active_call(center,hs[0]);html,expr=self.p4_rep_markup(hs[0].args[2],needle=(ac[0].start,ac[0].end));parser=TableHTMLContext(expr);parser.feed(html);parser.close()
        if not any(k in parser.active and x and(x[0].start,x[-1].end)==(ac[0].start,ac[0].end)for k,x in expr.items()):raise Unsupported('Workspace original actions are hidden in an inactive HTML context')
        self.p4_rep_push_event(actions,event,event['action_array'])
        if {s.get('kind')for s in flow['flow_steps']}!={'list_type_to_task_row','task_row_event_to_opening','opening_to_internal_task_route','task_context_to_header_actions','action_array_to_original_handler'}:raise Unsupported('Workspace source edges are incomplete')
        return dict(entry_evidence=self.evidence(event['evidence']),terminal_evidence=terminal['evidence'],terminal_symbol=terminal['symbol'],result_evidence=terminal['evidence'],result_symbol=terminal['symbol'])

    def p4_rep_literal_tab_control(self,body,event,spec):
        e=self.evidence(spec['evidence']);name=spec['variable'];wanted=spec['literal'];found=[]
        for ts in body['model'].lists:
            ms=matching(ts)
            for i,t in enumerate(ts[:-4]):
                if[x.value for x in ts[i:i+3]]!=['const',name,'=']or body['model'].enclosing_function(t.start)is not body['function']:continue
                if not self.p2_direct_statement(body['body'],t.start):raise Unsupported('Monitor tab builder is not an actual direct function-body binding')
                j=i+3
                while j<len(ts)and ts[j].value!=';':j=ms[j]+1 if ts[j].value in {'(','[','{'}else j+1
                if not(e.start<=t.start and ts[j].end<=e.end):continue
                rhs=ts[i+3:j]
                for piece in self.p4_rep_split(rhs,'+'):
                    if not piece or piece[0].value!='[':continue
                    pm=matching(piece);close=pm[0];pairs=[]
                    for row in split_tokens(piece[1:close]):
                        if not row or row[0].value!='['or matching(row).get(0)!=len(row)-1:raise Unsupported('Monitor tab choices are not original literal pairs')
                        pair=[self.p4_rep_literal(x)for x in split_tokens(row[1:-1])]
                        if len(pair)!=2 or not all(isinstance(x,str)for x in pair):raise Unsupported('Monitor tab choices contain a dynamic key/label')
                        pairs.append(pair)
                    selected=[p for p in pairs if p[0]==wanted]
                    if len(selected)!=1 or len({p[0]for p in pairs})!=len(pairs):raise Unsupported('Monitor selected tab key is missing or duplicated')
                    k=close+1
                    if[x.value for x in piece[k:k+5]]!=['.','map','(','(','[']:continue
                    arr=pm[k+4];names=[x[0].value for x in split_tokens(piece[k+5:arr])if len(x)==1 and x[0].kind=='id']
                    if len(names)!=2 or[x.value for x in piece[arr+1:arr+3]]!=[')','=>']:raise Unsupported('Monitor literal-pair mapper parameters differ')
                    mapclose=pm[k+2];expr=piece[arr+3:mapclose]
                    if[x.value for x in piece[mapclose+1:]]!=['.','join','(',"''",')']:raise Unsupported('Monitor tab controls are not joined without inserted HTML')
                    bindings=dict(zip(names,selected[0]));self.p4_rep_quoted_control(expr,event,bindings)
                    html,values=self.p4_rep_markup(rhs,needle=(piece[0].start,piece[-1].end));parser=TableHTMLContext(values);parser.feed(html);parser.close()
                    if not any(key in parser.active and x and(x[0].start,x[-1].end)==(piece[0].start,piece[-1].end)for key,x in values.items()):raise Unsupported('Monitor tab map is hidden by an inert outer HTML context')
                    found.append(ts[i+1])
        if len(found)!=1:raise Unsupported('Monitor original tab control lacks one actual finite literal-pair map')
        ret=self.p4_rep_return(body);refs=[t for t in ret if t.value==name]
        if len(refs)!=1:raise Unsupported('Monitor tabs are not consumed by its real returned body')
        html,values=self.p4_rep_markup(ret,needle=(refs[0].start,refs[0].end));parser=TableHTMLContext(values);parser.feed(html);parser.close()
        if not any(key in parser.active and x and x[0]is refs[0]for key,x in values.items()):raise Unsupported('Monitor tabs are inserted into an inactive body slot')
        self.p4_no_write_contexts(body['model'],{name},body['function'].body_start,body['function'].body_end,allowed=found)

    def p4_rep_seat_event_flow(self,flow,route,page,target,terminal):
        entry=flow['entry_binding'];event=flow['event_binding'];result=flow['result_binding'];embedding=self.p4_execution_monitor_frame(entry['monitor_embedding']);body=self.p4_rep_function(entry['body']);log=self.p4_rep_function(entry['event_log']);setter=self.p4_rep_function(entry['tab_setter']);opener=self.p4_rep_function(event['opening'])
        if body['function']is not embedding['body']['function']or opener['function']is not target['function']or terminal['function']is not page['function']:raise Unsupported('Seat event original embedded source/opening/result publications are disconnected')
        tab_event=entry['tab_event'];tp=self.p4_flow_export(tab_event['publisher'])
        if tp['function']is not setter['function']or setter['params']!=['value']:raise Unsupported('Monitor tab control is not its exact published original handler')
        self.p4_rep_literal_tab_control(body,tab_event,entry['tab_map'])
        pat=['monitorTab','=','value',';','refresh','(',')',';']
        if not any([x.value for x in setter['body'][i:i+len(pat)]]==pat for i in range(len(setter['body']))):raise Unsupported('Monitor event tab does not assign and refresh its actual tab state')
        refresh=self.p4_rep_function(entry['refresh']);pat=['node','.','innerHTML','=','body','(',')',';']
        if not any([x.value for x in refresh['body'][i:i+len(pat)]]==pat for i in range(len(refresh['body']))):raise Unsupported('Monitor tab refresh does not insert the actual original body into its DOM node')
        calls_log=self.p4_rep_calls(body,log['symbol'])
        if len(calls_log)!=1 or any(calls_log[0].args):raise Unsupported('Monitor event tab does not return its actual event-log producer')
        ret=self.p4_rep_return(body);html,values=self.p4_rep_markup(ret,bindings={'monitorTab':'events'},needle=(calls_log[0].start,calls_log[0].end));parser=TableHTMLContext(values);parser.feed(html);parser.close()
        if not any(key in parser.active and x and(x[0].start,x[-1].end)==(calls_log[0].start,calls_log[0].end)for key,x in values.items()):raise Unsupported('Monitor events branch does not consume its original event-log result')
        self.p4_rep_quoted_control(self.p4_rep_return(log),event)
        expr=self.p4_rep_return(opener);v=[t.value for t in expr]
        expected=['root','.','RouteRuntime','?.','openSecondary','?.','(',repr(route['route_key']),',','{','refreshOnClose',':','true','}',')','||','false']
        if v!=expected:raise Unsupported('Seat event opener does not invoke its exact original internal history route')
        table=self.p4_rep_function(result['table']);tc=self.p4_rep_calls(terminal,table['symbol'])
        if len(tc)!=1 or len(tc[0].args)!=1 or[x.value for x in tc[0].args[0]]!=['visible']:raise Unsupported('Seat history result does not return its actual paged local event rows')
        self.p4_rep_active_call(terminal,tc[0]);self.p1_table_contract(self.evidence(result['table_implementation_evidence']),self.evidence(result['table_export_evidence']),'at');ui=embedding['ui_alias'];self.p4_flow_ui_alias(entry['monitor_embedding']['ui_alias_evidence'],ui,table['parent'],table['function'].body_start+1)
        uc=self.p4_rep_calls(table,ui+'.table')
        if len(uc)!=1 or len(uc[0].args)<2 or[x.value for x in uc[0].args[1]]!=table['params']:raise Unsupported('Seat event history does not consume its original local rows in the real table')
        if {s.get('kind')for s in flow['flow_steps']}!={'workbench_embeds_monitor','literal_event_tab_to_body','event_log_to_open_history','internal_route_to_original_local_history'}:raise Unsupported('Seat event source edges are incomplete')
        return dict(entry_evidence=self.evidence(event['evidence']),terminal_evidence=terminal['evidence'],terminal_symbol=terminal['symbol'],result_evidence=terminal['evidence'],result_symbol=terminal['symbol'])

    # Execution-family active source flow. Candidate snippets only; root owns integration.

    def p4_execution_flow(self, flow):
        if not isinstance(flow, dict) or flow.get('family') != 'execution':
            raise Unsupported('Execution flow family is missing')
        binding = flow.get('entry_binding', {})
        variant = binding.get('version') if isinstance(binding, dict) else None
        if variant == 'bounded-queue-user-flow@1':
            return self.p4_queue_flow(flow)
        if variant == 'bounded-field-kind-flow@1':
            return self.p4_field_kind_flow(flow)
        if variant == 'bounded-application-view-flow@1':
            return self.p4_application_view_flow(flow)
        if variant == 'bounded-delegated-exception-flow@1':
            return self.p4_delegated_exception_flow(flow)
        if variant == 'bounded-captured-monitor-flow@1':
            return self.p4_captured_monitor_flow(flow)
        if variant == 'bounded-outbound-group-user-save@1':
            return self.p4_outbound_group_user_save_flow(flow)
        if variant == 'bounded-retry-user-save-flow@1':
            return self.p4_retry_user_save_flow(flow)
        raise Unsupported('Execution source flow needs one implemented finite original structure')

    def p4_execution_named(self, spec):
        if not isinstance(spec, dict) or not IDENT.fullmatch(spec.get('symbol', '')):
            raise Unsupported('Execution function needs one exact original symbol')
        e = self.evidence(spec.get('evidence')); model = e.model
        found = [f for f in model.functions if f.is_declaration and f.symbol == spec['symbol'] and e.contains(f)]
        if len(found) != 1:
            raise Unsupported('Execution evidence does not cover one unique original named function')
        fn = found[0]
        for ts in model.lists:
            ms = matching(ts)
            for i, token in enumerate(ts):
                if token.start == fn.start and token.value == 'function':
                    at = i + 2; close = ms.get(at)
                    if close is None or ts[at].value != '(' or ts[close+1].value != '{':
                        raise Unsupported('Execution original function header is unsupported')
                    params = split_tokens(ts[at+1:close]); params = [] if params == [[]] else params
                    if any(not p or p[0].kind != 'id' for p in params):
                        raise Unsupported('Execution profile only binds named original parameters')
                    return {'evidence':e,'model':model,'function':fn,'parent':fn.parent,'params':params,'body':ts[close+2:ms[close+1]]}
        raise Unsupported('Execution original function body is unavailable')

    def p4_execution_steps(self, flow):
        steps = flow.get('flow_steps')
        if not isinstance(steps, list) or not 1 <= len(steps) <= 20:
            raise Unsupported('Execution source flow needs finite named original steps')
        result = {}
        for step in steps:
            if not isinstance(step, dict) or step.get('kind') != 'function' or not IDENT.fullmatch(step.get('role','')) or step['role'] in result:
                raise Unsupported('Execution function roles are missing, duplicated or unsupported')
            result[step['role']] = self.p4_execution_named(step)
        return result

    def p4_execution_call(self, step, callee, evidence, *, allow_callback=False):
        e = self.evidence(evidence)
        if e.model is not step['model']:
            raise Unsupported('Execution callsite moved from its original function file')
        path = normalized_callee(callee)
        hits = [c for c in e.model.calls if e.contains(c) and c.path == path and (e.model.enclosing_function(c.start+0.5) is step['function'] or allow_callback and step['function'].body_start<c.start<step['function'].body_end)]
        if len(hits) != 1:
            raise Unsupported('Execution source edge lacks one exact actual callsite')
        call = hits[0]
        # This helper selects syntax only. Each family must separately prove
        # the call's selected branch/capture and result consumption.
        return call

    def p4_execution_return(self, step):
        body=step['body']; ms=matching(body); found=[]
        for i,t in enumerate(body):
            if t.value!='return' or step['model'].enclosing_function(t.start)is not step['function'] or not self.p4_execution_direct_statement(body,t.start):
                continue
            end=i+1
            while end<len(body) and body[end].value!=';':
                end=ms[end]+1 if body[end].value in {'(', '[', '{'} else end+1
            found.append(body[i+1:end])
        if len(found)!=1:
            raise Unsupported('Execution HTML producer lacks one direct returned expression')
        # Source conditional guards remain conditional; a constant true return
        # or an unconditional earlier return cannot establish a control.
        selected=found[0][0].start
        for i,t in enumerate(body[:-1]):
            if t.value=='if' and body[i+1].value=='(' and [x.value for x in body[i+2:ms[i+1]]]==['true'] and t.start<selected:
                raise Unsupported('Execution HTML producer has a deterministic blocking branch')
        return found[0]

    def p4_execution_concat(self, tokens):
        # Decode only quoted literals/templates and preserve opaque expressions
        # as markers. This is provenance markup, never a JS evaluator.
        tokens=self.p3_unwrap(tokens); ms=matching(tokens); parts=[]; start=0; i=0
        while i<len(tokens):
            if tokens[i].value in {'(', '[', '{'}:i=ms[i]+1;continue
            if tokens[i].value=='+':parts.append(tokens[start:i]);start=i+1
            i+=1
        parts.append(tokens[start:]); text=[]; expressions={}
        for part in parts:
            part=self.p3_unwrap(part)
            if len(part)==1 and part[0].kind=='string':text.append(self.p4_quoted_html(part[0]));continue
            if len(part)==1 and part[0].kind=='template':
                html,values=template_markup(part[0])
                for marker,value in values.items():
                    new='__EXECUTION_EXPR_'+str(len(expressions))+'__';html=html.replace(marker,new);expressions[new]=value
                text.append(html);continue
            if not part:raise Unsupported('Execution HTML concatenation has an empty operand')
            marker='__EXECUTION_EXPR_'+str(len(expressions))+'__';expressions[marker]=part;text.append(marker)
        return ''.join(text),expressions

    def p4_execution_constructor(self, spec, producer):
        if not isinstance(spec,dict)or spec.get('version')not in {'bounded-concat-fixed-handler@1','bounded-concat-owner-alias-handler@1'}:
            raise Unsupported('Execution event needs its finite fixed-owner constructor')
        e=self.evidence(spec.get('evidence'));name=spec.get('symbol');escape=spec.get('escape_symbol')
        if e.model is not producer['model']or not IDENT.fullmatch(name or '')or not IDENT.fullmatch(escape or ''):
            raise Unsupported('Execution event constructor is disconnected')
        found=[]
        for ts in e.model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts):
                if [x.value for x in ts[i:i+4]]!=['const',name,'=','(']:continue
                close=ms[i+3];params=ts[i+4:close]
                if len(params)!=4 or params[0].kind!='id'or [x.value for x in params[1:3]]!=[',','...']or params[3].kind!='id':continue
                at=close+2
                if ts[close+1].value!='=>'or [x.value for x in ts[at:at+2]]!=[escape,'(']:continue
                end=ms[at+1]
                if end+1>=len(ts)or ts[end+1].value!=';'or not(e.start<=t.start and ts[end+1].end<=e.end):continue
                rhs=ts[at+2:end];groups=[];j=0;start=0;rm=matching(rhs)
                while j<len(rhs):
                    if rhs[j].value in {'(', '[', '{'}:j=rm[j]+1;continue
                    if rhs[j].value=='+':groups.append(rhs[start:j]);start=j+1
                    j+=1
                groups.append(rhs[start:])
                if len(groups)!=5 or any(len(groups[n])!=1 or groups[n][0].kind!='string'for n in [0,2,4])or [x.value for x in groups[1]]!=[params[0].value]or literal(groups[2][0])!='('or literal(groups[4][0])!=')':continue
                prefix=self.p4_quoted_html(groups[0][0]);owner=normalized_callee(prefix.removesuffix('.'))
                if not owner or not prefix.endswith('.'):continue
                args=groups[3];maps=[c for c in calls(args)if c.path==(params[3].value,'map')and c.index==0]
                if len(maps)!=1 or len(maps[0].args)!=1:continue
                mapper=maps[0].args[0]
                if len(mapper)!=8 or mapper[0].kind!='id'or [x.value for x in mapper[1:]]!=['=>','JSON','.','stringify','(',mapper[0].value,')']:continue
                tail=args[maps[0].end_index+1:]
                if len(tail)!=5 or [x.value for x in tail[:3]]!=['.','join','(']or literal(tail[3])!=','or tail[4].value!=')':continue
                if e.model.scope(t.start)is not producer['parent']:continue
                found.append((owner,t,ts[i+1]))
        if len(found)!=1:raise Unsupported('Execution event constructor is not the exact escaped fixed-owner/JSON argument grammar')
        owner,decl,name_token=found[0]
        self.p2_no_owner_writes(e.model,[(name,)],[name_token]);self.no_shadow(e.model,name,producer['parent'],[producer['function'].start])
        if spec.get('version')=='bounded-concat-owner-alias-handler@1':
            alias=spec.get('ui_alias')
            self.p4_flow_ui_alias(spec.get('ui_alias_evidence'),alias,producer['parent'],producer['function'].body_start+1)
            self.p2_const_alias(self.evidence(spec.get('escape_alias_evidence')),escape,alias+'.escape',producer['parent'],producer['function'].body_start+1)
            self.p2_escape(self.evidence(spec.get('escape_implementation_evidence')),self.evidence(spec.get('escape_export_evidence')))
            return {'name':name,'owner':owner,'escape':escape}
        factory=self.p4_flow_factory(spec.get('ui_factory'))
        if factory['model']is not e.model or factory['parent']is not producer['parent']or factory['owner']not in {('PlatformUI',),('window','PlatformUI')}:
            raise Unsupported('Execution escaping UI factory is not the actual PlatformUI owner')
        self.no_shadow(e.model,factory['symbol'],producer['parent'],[producer['function'].body_start+1])
        self.no_shadow(e.model,escape,producer['parent'],[producer['function'].body_start+1])
        ee=self.evidence(spec.get('escape_binding_evidence'));values=[]
        for ts in ee.model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts[:-10]):
                if [x.value for x in ts[i:i+3]]!=['const',escape,'=']or ts[i+3].kind!='id'or [x.value for x in ts[i+4:i+9]]!=['=>',factory['symbol'],'(',')','.']:continue
                v=ts[i+3].value
                if [x.value for x in ts[i+9:i+14]]!=['escape','(',v,')',';']:continue
                if ee.start<=t.start and ts[i+13].end<=ee.end and ee.model.scope(t.start)is producer['parent']:values.append(ts[i+1])
        if ee.model is not e.model or len(values)!=1:raise Unsupported('Execution escape wrapper is not one pure original UI factory escape call')
        self.p2_no_owner_writes(e.model,[(escape,)],[values[0]]);self.no_shadow(e.model,'JSON',None,[decl.start],include_parent=True)
        self.p2_escape(self.evidence(spec.get('escape_implementation_evidence')),self.evidence(spec.get('escape_export_evidence')))
        return {'name':name,'owner':owner,'escape':escape}

    def p4_execution_html_event(self,tokens,event,callee,constructor=None):
        html,expressions=self.p4_execution_concat(tokens);parser=TableHTMLContext(expressions);parser.feed(html);parser.close();hits=[]
        for actual,handler in parser.handlers:
            if actual!=event:continue
            value=handler.strip()
            if event=='submit'and value.startswith('event.preventDefault();'):value=value[len('event.preventDefault();'):]
            value=re.sub(r';\s*return\s+false\s*;?\s*$','',value).strip().removesuffix(';').strip()
            if value in expressions:
                cs=calls(expressions[value])
                if constructor and len(cs)==1 and cs[0].path==(constructor['name'],)and cs[0].index==0 and cs[0].end_index==len(expressions[value])-1 and cs[0].args and len(cs[0].args[0])==1:
                    method=literal(cs[0].args[0][0])
                    if constructor['owner']+(method,)==normalized_callee(callee):hits.append(cs[0].args[1:])
            else:
                try:cs=calls(lex(value))
                except Unsupported:continue
                if len(cs)==1 and cs[0].path==normalized_callee(callee):
                    args=[]
                    for arg in ([] if not any(cs[0].args) else cs[0].args):
                        if len(arg)==1 and arg[0].kind=='string'and literal(arg[0])in expressions:args.append(expressions[literal(arg[0])])
                        else:args.append(arg)
                    hits.append(args)
        if len(hits)!=1:raise Unsupported('Execution HTML lacks one exact active real event for the selected original operation')
        return hits[0]

    def p4_execution_ui_path(self, producer, spec):
        if not isinstance(spec,dict)or spec.get('version')!='bounded-returned-ui-path@1':raise Unsupported('Execution control needs an explicit returned UI path')
        node=self.p4_execution_return(producer);path=spec.get('path');context={'row_parameters':None,'column_key':None}
        if not isinstance(path,list)or len(path)>10:raise Unsupported('Execution returned UI path is unbounded')
        for step in path:
            if not isinstance(step,dict):raise Unsupported('Execution UI path step is malformed')
            kind=step.get('kind')
            if kind=='active_template_expression':
                index=step.get('index')
                if len(node)!=1 or node[0].kind!='template' or type(index)is not int:raise Unsupported('Execution source path requires one exact literal template')
                parts=[part for k,part in node[0].parts if k=='expression']
                if not 0<=index<len(parts)or not self.p1_active_part(node[0],parts[index]):raise Unsupported('Execution source path selects an inert/attribute/unreturned slot')
                node=parts[index]
            elif kind=='ui_call_argument':
                target=normalized_callee(step.get('callee'));index=step.get('argument_index');matches=[c for c in calls(node)if c.path==target]
                if len(matches)!=1 or type(index)is not int or not 0<=index<len(matches[0].args):raise Unsupported('Execution path does not select one actual UI call/argument')
                call=matches[0]
                self.p4_execution_active_term(node,call)
                slot=self.p4_flow_ui_slot(step.get('slot'))
                if slot['index']!=index or slot['function'].symbol!=target[-1]:raise Unsupported('Execution returned HTML argument differs from real UI consumption')
                # The selected UI helper call is a real term of the return, not
                # a call buried inside an unused callback or inert literal.
                top=self.p3_unwrap(node);parts=[];ms=matching(top);start=0;i=0
                while i<len(top):
                    if top[i].value in {'(', '[', '{'}:i=ms[i]+1;continue
                    if top[i].value=='+':parts.append(top[start:i]);start=i+1
                    i+=1
                parts.append(top[start:])
                if not any(p and p[0].start==call.start and p[-1].end==call.end for p in parts):raise Unsupported('Execution UI helper is not a direct actual return term')
                node=call.args[index]
            elif kind=='table_column':
                calls_found=[c for c in calls(node)if c.path==normalized_callee(step.get('callee'))]
                if len(calls_found)!=1 or len(calls_found[0].args)<2:raise Unsupported('Execution row control lacks the real returned table')
                call=calls_found[0];self.p4_execution_active_term(node,call);columns=call.args[0];index=step.get('index')
                if not columns or columns[0].value!='['or matching(columns).get(0)!=len(columns)-1:raise Unsupported('Execution table columns are not literal')
                cols=[p for p in split_tokens(columns[1:-1])if p]
                if type(index)is not int or not 0<=index<len(cols):raise Unsupported('Execution table column index differs')
                props=self.p1_props(cols[index]);key=self.p1_literal(props.get('key',[]))
                if key!=step.get('key'):raise Unsupported('Execution table source object key differs')
                self.p1_table_contract(self.evidence(step.get('table_implementation_evidence')),self.evidence(step.get('table_export_evidence')),key)
                value=props.get('render',[]);vm=matching(value)
                if value and value[0].value=='(':close=vm[0];params=split_tokens(value[1:close]);at=close+2
                elif len(value)>2 and value[0].kind=='id'and value[1].value=='=>':params=[[value[0]]];at=2
                else:raise Unsupported('Execution table control has no exact literal return arrow')
                if at>=len(value)or any(len(p)!=1 or p[0].kind!='id'for p in params):raise Unsupported('Execution renderer parameters are unsupported')
                node=value[at:]
                if node[0].value=='{':raise Unsupported('Execution table callback body needs a separate finite profile')
                context['row_parameters']=[p[0].value for p in params];context['column_key']=key
                self.p4_no_write_contexts(producer['model'],set(context['row_parameters']),node[0].start-1,node[-1].end+1)
                self.p2_state_mutators(producer['model'],set(context['row_parameters']),node[0].start-1,node[-1].end+1,label='Execution renderer identity')
            else:raise Unsupported('Unknown execution returned UI path step')
        return dict(context,tokens=node)

    def p4_execution_active_term(self,tokens,call):
        markup,expressions=self.p4_execution_concat(tokens);parser=TableHTMLContext(expressions);parser.feed(markup);parser.close()
        hits=[marker for marker,part in expressions.items()if part and part[0].start==call.start and part[-1].end==call.end and marker in parser.active]
        if len(hits)!=1:raise Unsupported('Execution UI consumer is not a direct active returned HTML term')

    def p4_execution_const(self,step,name):
        values=[]
        for ts in step['model'].lists:
            ms=matching(ts)
            for i,t in enumerate(ts):
                if t.value not in {'const','let'}or step['model'].enclosing_function(t.start)is not step['function']or not self.p4_execution_direct_statement(step['body'],t.start):continue
                end=i+1
                while end<len(ts)and ts[end].value!=';':end=ms[end]+1 if ts[end].value in {'(', '[', '{'}else end+1
                for part in split_tokens(ts[i+1:end]):
                    if len(part)>=3 and part[0].value==name and part[1].value=='=':
                        if not self.p4_execution_direct_statement(step['body'],t.start):raise Unsupported('Execution source value comes from a guarded/dead declaration: '+name)
                        values.append((part[0],part[2:]))
        if len(values)!=1:raise Unsupported('Execution source value lacks one unique original local binding: '+str(name))
        return values[0]

    def p4_execution_tokens(self,tokens):
        return [('literal',literal(t))if t.kind=='string'else('token',t.value)for t in tokens]

    def p4_execution_pattern(self,step,source,*,root=False,within=None):
        expected=self.p4_execution_tokens(lex(source));hits=[]
        for ts in step['model'].lists:
            for i in range(len(ts)-len(expected)+1):
                part=ts[i:i+len(expected)]
                if not part or self.p4_execution_tokens(part)!=expected or step['model'].enclosing_function(part[0].start+0.5)is not step['function']:continue
                if within is not None and not(within[0]<=part[0].start and part[-1].end<=within[1]):continue
                if root and not self.p4_execution_direct_statement(step['body'],part[0].start):continue
                hits.append(part)
        if len(hits)!=1:raise Unsupported('Execution source structure is absent, duplicated or disconnected: '+source[:90])
        return hits[0]

    def p4_execution_equal(self,tokens,source):
        if self.p4_execution_tokens(tokens)!=self.p4_execution_tokens(lex(source)):
            raise Unsupported('Execution selected value/call differs from its original finite source flow')

    def p4_application_view_flow(self,flow):
        op=flow['entry_binding'];steps=self.p4_execution_steps(flow);kind=op.get('operation')
        claimed=self.proof.get('source_chain',{}).get('entry',{});event_claim=flow.get('event_binding',{})
        if normalized_callee(claimed.get('callee'))!=normalized_callee(event_claim.get('callee'))or claimed.get('event')!=event_claim.get('event'):raise Unsupported('Execution claimed entry differs from the actual checked event')
        if kind not in {'query','create','edit','delete','detail'}:raise Unsupported('Unsupported application view operation')
        names=op.get('names',{});required=['view','selected_id','body','list_result','row','options','input','save_result','editing']
        if not isinstance(names,dict)or any(not IDENT.fullmatch(names.get(k,''))for k in required):raise Unsupported('Application flow must bind exact original state/parameter names')
        for role in ['page_render','list_render','form_render','detail_render','go','control']:
            if role not in steps:raise Unsupported('Application flow is missing a real source function: '+role)
        page=steps['page_render'];view=names['view'];selected=names['selected_id'];bodyname=names['body'];res=names['list_result'];row=names['row'];options=names['options']
        pub=self.p4_flow_export(op.get('page_publication'));route=self.p4_flow_route(op.get('route'))
        if pub['function']is not page['function']or pub['model']is not page['model']or route['route_page']!=op.get('page_key'):
            raise Unsupported('Application flow is not the registered original page render')
        ui=self.p4_flow_factory(op.get('ui_factory'));runtime=self.p4_flow_factory(op.get('runtime_factory'))
        if ui['model']is not page['model']or runtime['model']is not page['model']or ui['parent']is not page['parent']or runtime['parent']is not page['parent']:
            raise Unsupported('Application runtime/UI factories moved from their real page scope')
        # The same result supplies list rows; selected row supplies detail/form.
        list_token,list_rhs=self.p4_execution_const(page,res)
        self.p4_execution_equal(list_rhs,runtime['symbol']+'().listApplications()')
        body_token,body_rhs=self.p4_execution_const(page,bodyname)
        self.p4_execution_equal(body_rhs,view+"==='list'?"+steps['list_render']['function'].symbol+'('+res+'.data):'+view+"==='detail'?"+steps['detail_render']['function'].symbol+'('+row+'):'+steps['form_render']['function'].symbol+'('+row+')')
        row_token,row_rhs=self.p4_execution_const(page,row)
        # A selected row must come from that exact guarded list result.
        rv=[t.value for t in row_rhs]
        if len(rv)!=15 or rv[:5]!=[res,'.','data','.','find']or rv[5]!='('or rv[6]!='item'or rv[7]!='=>'or rv[8:14]!=['item','.','applicationId','===',selected,')']:
            # Keep the finite callback whole, without substring qualification.
            self.p4_execution_equal(row_rhs,res+'.data.find(item=>item.applicationId==='+selected+')')
        self.p4_application_frame_state(page,names,[list_token,row_token,body_token])
        return_expr=self.p4_execution_return(page)
        markup,values=self.p4_execution_concat(return_expr);hp=TableHTMLContext(values);hp.feed(markup);hp.close()
        if not any([t.value for t in part]==[bodyname]and marker in hp.active for marker,part in values.items()):raise Unsupported('Application selected body is not consumed by real active page HTML')
        self.no_shadow(page['model'],ui['symbol'],page['parent'],[page['function'].body_start+1]);self.no_shadow(page['model'],runtime['symbol'],page['parent'],[page['function'].body_start+1])
        go=steps['go'];gp=[p[0].value for p in go['params']]
        if len(gp)!=2:raise Unsupported('Application navigation must preserve its two original view/id arguments')
        writes=self.p4_execution_pattern(go,view+'='+gp[0]+';'+selected+'='+gp[1]+"||'';",root=True)
        navigate=self.p4_execution_pattern(go,"return window.navigateTo("+repr(op['page_key'])+",{applicationView:"+view+',applicationId:'+selected+',preserveFilters:true});',root=True)
        self.p4_no_write_contexts(go['model'],{view,selected},go['function'].body_start,go['function'].body_end,[writes[0],writes[4]])
        # Original render accepts only the same four private application views,
        # forwards applicationId unchanged, and preserves the selected body.
        self.p4_execution_pattern(page,"if(!contextChanged&&"+options+".applicationView){")
        self.p4_execution_pattern(page,"const nextView=['list','create','detail','edit'].includes("+options+'.applicationView)?'+options+".applicationView:'list';")
        self.p4_execution_pattern(page,view+'=nextView;'+selected+'='+options+".applicationId||'';")
        control=steps['control'];event=flow.get('event_binding',{});cp=self.p4_flow_export(event.get('control_publication'))
        if cp['function']is not control['function']or cp['model']is not page['model']:raise Unsupported('Application event targets another original implementation')
        producer=steps['form_render']if kind in {'create','edit'}else steps['list_render']
        ctor=self.p4_execution_constructor(event.get('constructor'),producer)
        path=self.p4_execution_ui_path(producer,event.get('ui_path'))
        arguments=self.p4_execution_html_event(path['tokens'],event.get('event'),event.get('callee'),ctor)
        if normalized_callee(event.get('callee'))!=cp['callee']:raise Unsupported('Application event receiver differs from its exact publication')
        if kind in {'query','create','edit'}and arguments:raise Unsupported('Application form/query event has unexpected identity arguments')
        if kind in {'detail','delete'}:
            params=path.get('row_parameters');key=path.get('column_key')
            if not params or key!='applicationId'or len(arguments)!=1 or [t.value for t in arguments[0]]!=[params[0]]:raise Unsupported('Application row control does not preserve the exact applicationId')
        rb=flow.get('result_binding',{});result_step=self.p4_execution_named(rb)
        component=None if kind=='detail'else self.p4_flow_export(rb.get('publication'))
        if component is not None and (component['function']is not result_step['function']or component['model']is not result_step['model']):raise Unsupported('Application result is not its actual original static publication')
        if kind=='query':
            self.p4_execution_pattern(control,'return '+steps['refresh']['function'].symbol+'();',root=True)
            self.p4_execution_pattern(control,"keyword=document.getElementById('dccApplicationKeyword')?.value.trim()||'';")
            if result_step['function']is not runtime['publication']['function']or component['callee'][-1]!='listApplications':raise Unsupported('Application query does not consume the original guarded list result')
        elif kind in {'create','edit'}:
            if control['function'].symbol!=event.get('save_symbol'):raise Unsupported('Application form event is not the actual save handler')
            open_step=steps['open_control'];open_pub=self.p4_flow_export(event.get('opening_publication'))
            if open_pub['function']is not open_step['function']:raise Unsupported('Application opening event lacks its exact source export')
            open_producer=steps['list_render'];opening_path=self.p4_execution_ui_path(open_producer,event.get('opening_ui_path'))
            opening_args=self.p4_execution_html_event(opening_path['tokens'],'click',event.get('opening_callee'),self.p4_execution_constructor(event.get('constructor'),open_producer))
            if normalized_callee(event.get('opening_callee'))!=open_pub['callee']:raise Unsupported('Application opening HTML targets another original owner')
            if kind=='create':
                if opening_args:raise Unsupported('New application opening has an unexpected existing identity')
                self.p4_execution_equal(self.p4_execution_return(open_step),go['function'].symbol+"('create')")
            else:
                pp=[p[0].value for p in open_step['params']];rp=opening_path.get('row_parameters')
                if len(pp)!=1 or not rp or opening_path.get('column_key')!='applicationId'or len(opening_args)!=1 or [t.value for t in opening_args[0]]!=[rp[0]]:raise Unsupported('Application edit opening loses its exact original row id')
                self.p4_execution_equal(self.p4_execution_return(open_step),steps['lookup']['function'].symbol+'('+pp[0]+')?'+go['function'].symbol+"('edit',"+pp[0]+'):false')
            editing=names['editing'];save_result=names['save_result'];inputname=names['input'];et,er=self.p4_execution_const(control,editing)
            self.p4_execution_equal(er,view+"==='edit'")
            result_token,result_rhs=self.p4_execution_const(control,save_result)
            self.p4_execution_equal(result_rhs,editing+'?'+runtime['symbol']+'().saveApplication('+inputname+'):'+runtime['symbol']+'().createApplication('+inputname+')')
            wanted='saveApplication'if kind=='edit'else'createApplication'
            if component['callee'][-1]!=wanted or runtime['publication']['canonical_callee'][:-1]!=component['canonical_callee'][:-1]:raise Unsupported('Application selected view resolves to another component/result')
            identity_write=self.p4_execution_pattern(control,'if('+editing+')'+inputname+'.applicationId='+selected+';')
            input_token,input_rhs=self.p4_execution_const(control,inputname)
            self.p4_no_write_contexts(control['model'],{inputname},control['function'].body_start,control['function'].body_end,[input_token,identity_write[4]])
            input_calls=[c for c in control['model'].calls if result_rhs[0].start<c.start<c.end<=result_rhs[-1].end and c.path in {(runtime['symbol']+'()','saveApplication'),(runtime['symbol']+'()','createApplication')}]
            self.p2_protected_escapes(control['model'],{inputname},input_rhs[-1].end,control['function'].body_end,[arg[0]for c in input_calls for arg in c.args if len(arg)==1 and arg[0].value==inputname],label='Application selected input')
            self.p4_execution_pattern(control,'if(!'+save_result+'.ok){')
            self.p4_execution_pattern(control,go['function'].symbol+"('detail',"+save_result+'.data.applicationId);',root=True)
            self.p4_no_write_contexts(control['model'],{editing,save_result},control['function'].body_start,control['function'].body_end,[et,result_token])
            self.p2_state_mutators(control['model'],{editing,save_result},result_rhs[-1].end,control['function'].body_end,label='Application save result')
            result_returns=[ts[i+1]for ts in control['model'].lists for i,t in enumerate(ts[:-1])if t.value=='return'and ts[i+1].value==save_result and control['function'].body_start<t.start<control['function'].body_end]
            self.p2_protected_escapes(control['model'],{save_result},result_rhs[-1].end,control['function'].body_end,result_returns,label='Application result')
        elif kind=='detail':
            pp=[p[0].value for p in control['params']]
            if len(pp)!=1:raise Unsupported('Application detail opening lacks its original identity parameter')
            self.p4_execution_equal(self.p4_execution_return(control),steps['lookup']['function'].symbol+'('+pp[0]+')?'+go['function'].symbol+"('detail',"+pp[0]+'):false')
            if result_step['function']is not steps['detail_render']['function']:raise Unsupported('Application detail result is not the selected original returned detail renderer')
        else:
            self.p4_application_delete_result(flow,steps,control,result_step,component,runtime)
        terminal=self.p4_execution_named(flow.get('terminal_binding',rb))
        return {'entry_evidence':control['evidence'],'terminal_evidence':terminal['evidence'],'terminal_symbol':terminal['function'].symbol,'result_evidence':result_step['evidence'],'result_symbol':result_step['function'].symbol,'followup_verified':False}

    def p4_execution_confirm_callback(self,spec,caller,ui_factory):
        if not isinstance(spec,dict)or spec.get('version')!='bounded-confirm-callback@1':raise Unsupported('Execution component commit needs the real confirmation callback')
        library=self.p4_execution_named(spec.get('library'));pub=self.p4_flow_export(spec.get('library',{}).get('publication'))
        if ui_factory.get('kind')=='const_alias':
            self.p4_flow_ui_alias(ui_factory.get('evidence'),ui_factory.get('symbol'),caller['parent'],caller['function'].body_start+1)
            if ui_factory.get('owner')!=('window','PlatformUI'):raise Unsupported('Execution confirmation alias targets another UI owner')
            call_path=(ui_factory['symbol'],'confirm')
        else:call_path=(ui_factory['symbol']+'()','confirm')
        if pub['function']is not library['function']or pub['canonical_callee'][:-1]!=ui_factory['owner']:raise Unsupported('Execution confirmation helper is not the real published UI receiver')
        params=[p[0].value for p in library['params']]
        if len(params)!=1:raise Unsupported('Execution confirmation option source differs')
        optname=spec.get('options_symbol');idname=spec.get('id_symbol');button=spec.get('button_symbol')
        if any(not IDENT.fullmatch(x or '')for x in [optname,idname,button]):raise Unsupported('Execution confirm source names are missing')
        _,opts=self.p4_execution_const(library,optname);self.p4_execution_equal(opts,params[0]+'||{}')
        _,ids=self.p4_execution_const(library,idname);self.p4_execution_equal(ids,optname+".id||'platform-confirm'")
        _,target=self.p4_execution_const(library,button);self.p4_execution_equal(target,'document.getElementById(`${'+idname+'}-confirm`)')
        self.p4_execution_pattern(library,'if('+button+')'+button+".onclick=()=>{if(typeof "+optname+".onConfirm==='function'&&"+optname+'.onConfirm()===false)return;closeLayer('+idname+');};',root=True)
        layer_calls=[c for c in library['model'].calls if c.path==('openLayer',)and library['model'].enclosing_function(c.start)is library['function']and len(c.args)>=2]
        if len(layer_calls)!=1 or [t.value for t in layer_calls[0].args[0]]!=[idname]or len(layer_calls[0].args[1])!=1 or layer_calls[0].args[1][0].kind!='template':raise Unsupported('Execution confirmation button is not mounted by original openLayer')
        slot=self.p4_flow_ui_slot(spec.get('open_layer_slot'))
        if slot['function'].symbol!='openLayer'or slot['index']!=1:raise Unsupported('Execution confirmation HTML is not the actual real DOM slot')
        html,values=template_markup(layer_calls[0].args[1][0]);expected=[marker+'-confirm'for marker,value in values.items()if[t.value for t in value]==[idname]and 'id="'+marker+'-confirm"'in html]
        if len(expected)!=1 or 'id="'+expected[0]+'"'not in html:raise Unsupported('Execution confirmation listener points to another button')
        ce=self.evidence(spec.get('callsite_evidence'));calls_found=[c for c in caller['model'].calls if ce.contains(c)and c.path==call_path and caller['model'].enclosing_function(c.start)is caller['function']and len(c.args)==1]
        if ce.model is not caller['model']or len(calls_found)!=1:raise Unsupported('Execution confirmation is not the actual original call')
        if not self.p4_execution_direct_statement(caller['body'],calls_found[0].start):
            guard=ui_factory.get('guarded_call');verified=False
            if isinstance(guard,dict)and guard.get('call')is calls_found[0]and isinstance(guard.get('tokens'),list)and guard['tokens']and all(isinstance(t,Token)for t in guard['tokens']):
                actual=guard['tokens']
                for ts in caller['model'].lists:
                    ms=matching(ts)
                    for i,t in enumerate(ts):
                        if t is not actual[0]or not all(a is b for a,b in zip(actual,ts[i:i+len(actual)]))or t.value!='if'or ts[i+1].value!='('or not self.p4_execution_direct_statement(caller['body'],t.start):continue
                        end=ms[i+1];start=end+1
                        if ts[start].value=='{'and ts[start]is actual[-1]and ts[start].start<calls_found[0].start<calls_found[0].end<ts[ms[start]].end:verified=True
            if not verified:raise Unsupported('Execution confirmation is not the actual direct or proved original guarded call')
        call=calls_found[0];arg=call.args[0];am=matching(arg)
        if not arg or arg[0].value!='{'or am.get(0)!=len(arg)-1:raise Unsupported('Execution confirmation options are not literal')
        callbacks=[]
        for part in split_tokens(arg[1:-1]):
            if len(part)>3 and [t.value for t in part[:3]]==['onConfirm','(',')']and part[3].value=='{':
                callback=caller['model'].enclosing_function(part[4].start)
                if callback is None or callback.parent is not caller['function']:raise Unsupported('Execution confirmation callback is from another lexical operation')
                callbacks.append({'function':callback,'model':caller['model'],'parent':callback.parent,'body':part[4:-1],'params':[],'evidence':ce})
            elif len(part)>3 and [t.value for t in part[:2]]==['onConfirm',':']:
                arrow=part[2:]
                if len(arrow)>3 and [t.value for t in arrow[:3]]==['(',')','=>']and arrow[3].value!='{':
                    expr=arrow[3:];edge=[c for c in calls(expr)if c.index==0 and c.end_index==len(expr)-1]
                    cb=caller['model'].enclosing_function(expr[0].start+0.5)
                    if len(edge)!=1 or cb is None or cb.parent is not caller['function']:raise Unsupported('Execution confirmation expression is not its one actual lexical callback call')
                    callbacks.append({'function':cb,'model':caller['model'],'parent':cb.parent,'body':expr,'params':[],'evidence':ce,'expression_only':True})
                else:callbacks.append(self.p4_execution_callback_step(caller,arrow))
        if len(callbacks)!=1:raise Unsupported('Execution originating operation lacks one real onConfirm method')
        return callbacks[0]

    def p4_application_delete_result(self,flow,steps,control,result_step,component,runtime):
        op=flow['entry_binding'];event=flow['event_binding'];rb=flow['result_binding'];params=[p[0].value for p in control['params']]
        if len(params)!=1 or component['callee'][-1]!='deleteApplication':raise Unsupported('Application delete must preserve the original identity/component')
        identity=params[0];ui=self.p4_flow_factory(op.get('ui_factory'));callback=self.p4_execution_confirm_callback(event.get('confirmation'),control,ui)
        source=self.p4_execution_pattern(control,'const originalContext=context;',root=True)
        self.p4_execution_pattern(callback,'if(!allowed()||context!==originalContext||!application('+identity+')){')
        token,rhs=self.p4_execution_const(callback,'result')
        self.p4_execution_equal(rhs,runtime['symbol']+'().deleteApplication({applicationId:'+identity+',expectedRevision:latest.data.revision})')
        self.p4_execution_pattern(callback,'if(!result.ok){')
        self.p4_execution_pattern(callback,"go('list');",root=True)
        self.p4_execution_pattern(callback,'return true;',root=True)
        self.p4_no_write_contexts(callback['model'],{'result'},callback['function'].body_start,callback['function'].body_end,[token])
        self.p2_state_mutators(callback['model'],{'result'},rhs[-1].end,callback['function'].body_end,label='Application delete component result')

    def p4_execution_direct_statement(self,body,position):
        ms=matching(body)
        def end_statement(at,depth=0):
            if depth>32 or at>=len(body):raise Unsupported('Execution statement structure is unbounded')
            if body[at].value=='{':return ms[at]+1
            if body[at].value=='try':
                end=end_statement(at+1,depth+1)
                if end<len(body)and body[end].value=='catch':
                    end=end_statement(ms[end+1]+1,depth+1)if body[end+1].value=='('else end_statement(end+1,depth+1)
                if end<len(body)and body[end].value=='finally':end=end_statement(end+1,depth+1)
                return end
            if body[at].value in {'if','while','for','switch'}:
                if at+1>=len(body)or body[at+1].value!='(':raise Unsupported('Execution original if guard is malformed')
                end=end_statement(ms[at+1]+1,depth+1)
                if end<len(body)and body[end].value=='else':end=end_statement(end+1,depth+1)
                return end
            while at<len(body)and body[at].value!=';':at=ms[at]+1 if body[at].value in {'(', '[', '{'}else at+1
            return min(at+1,len(body))
        at=0
        while at<len(body):
            if body[at].start==position:return True
            end=end_statement(at)
            if any(t.start==position for t in body[at:end]):return False
            if end<=at:raise Unsupported('Execution statement scan did not advance')
            at=end
        return False

    def p4_execution_bound_step(self,spec):
        bound=self.p4_flow_export(spec);fn,names,body=self.p4_flow_body(bound)
        return {'function':fn,'model':bound['model'],'parent':bound['parent'],'body':body,'params':[],'param_names':names,'evidence':bound['evidence'],'publication':bound}

    def p4_execution_component_frame(self,flow,steps):
        op=flow['entry_binding'];frame=steps['frame_render'];producer=steps['render'];route=self.p4_flow_route(op.get('route'))
        page=self.p4_flow_export(op.get('page_publication'));component=self.p4_flow_export(op.get('component_publication'))
        if page['function']is not frame['function']or component['function']is not producer['function']or component['model']is not producer['model']:
            raise Unsupported('Delegated execution frame differs from its actual static published functions')
        params=[p[0].value for p in frame['params']]
        if len(params)!=1 or route['route_view']!=op.get('selected_view'):raise Unsupported('Delegated execution page view does not match the real route')
        callee=op.get('component_callee')
        if normalized_callee(callee)!=component['callee']:raise Unsupported('Delegated execution receiver is not the actual published component')
        self.p4_execution_pattern(frame,'if('+params[0]+"?.view==="+repr(op['selected_view'])+')return '+callee+'();',root=True)
        alias=self.p4_flow_ui_alias(op.get('ui_alias_evidence'),op.get('ui_alias'),producer['parent'],producer['function'].body_start+1)
        return {'frame':frame,'render':producer,'component':component,'ui_alias':alias,'route':route}

    def p4_delegated_exception_flow(self,flow):
        op=flow['entry_binding'];kind=op.get('operation');steps=self.p4_execution_steps(flow)
        claimed=self.proof.get('source_chain',{}).get('entry',{});event_claim=flow.get('event_binding',{})
        if normalized_callee(claimed.get('callee'))!=normalized_callee(event_claim.get('callee'))or claimed.get('event')!='click':raise Unsupported('Exception claimed entry differs from its actual checked source event')
        if kind not in {'query','detail','recheck'}:raise Unsupported('Unsupported delegated exception operation')
        frame=self.p4_execution_component_frame(flow,steps);render=frame['render'];alias=frame['ui_alias'];event=flow.get('event_binding',{})
        # The list shown to the operator comes from the same actual filter
        # state changed by the published query method, never an unrelated list.
        self.p4_execution_pattern(render,'const all=rows(),visible=all.filter(r=>(status===\'全部\'||(status===\'待处理\'?r.status!==\'已恢复\':r.status===status))&&(!keyword||[r.callId,r.callee,CloudCallRuntime.tenant(r.tenantId)?.name].join(\' \').includes(keyword)));',root=True)
        if kind=='query':
            query=self.p4_execution_bound_step(event.get('control_publication'));control=event.get('callee')
            if query['model']is not render['model']or query['publication']['callee']!=normalized_callee(control):raise Unsupported('Exception query is from another component source')
            html=self.p4_execution_return(render);args=self.p4_execution_html_event(html,'click',control)
            if args:raise Unsupported('Exception query has unexpected caller supplied object identity')
            self.p4_execution_pattern(query,"page=1;keyword=document.getElementById('callIssueKeyword')?.value.trim()||'';status=document.getElementById('callIssueStatus')?.value||'待处理';refresh();",root=True)
            refresh=steps['refresh'];self.p4_execution_pattern(refresh,"if(allowed())navigateTo("+repr(frame['route']['route_key'])+');',root=True)
            result=render;entry=query
        else:
            opening=steps['open'];pub=self.p4_flow_export(event.get('opening_publication'))
            if pub['function']is not opening['function']or pub['model']is not render['model']:raise Unsupported('Exception detail/opening is not the original component function')
            selected=self.p4_execution_ui_path(render,event.get('opening_ui_path'))
            args=self.p4_execution_html_event(selected['tokens'],'click',event.get('opening_callee'))
            ps=selected.get('row_parameters')
            if not ps or selected.get('column_key')!='issueId'or len(args)!=1 or[t.value for t in args[0]]!=[ps[0]]or normalized_callee(event.get('opening_callee'))!=pub['callee']:
                raise Unsupported('Exception opening does not bind the same real issueId')
            pp=[p[0].value for p in opening['params']]
            if len(pp)!=1:raise Unsupported('Exception detail has no exact original issueId parameter')
            ident=pp[0]
            row_token,row_rhs=self.p4_execution_const(opening,'r');self.p4_execution_equal(row_rhs,'find('+ident+')')
            self.p4_execution_pattern(opening,'if(!r)return;')
            token,capture=self.p4_execution_const(opening,'token');self.p4_execution_equal(capture,'context()')
            ce=self.evidence(event.get('layer_evidence'));layers=[c for c in opening['model'].calls if ce.contains(c)and c.path==(alias,'openLayer')and opening['model'].enclosing_function(c.start)is opening['function']and len(c.args)>=2]
            if len(layers)!=1 or len(layers[0].args[0])!=1 or literal(layers[0].args[0][0])!=event.get('layer_id'):raise Unsupported('Exception result is not in the actual original layer')
            slot=self.p4_flow_ui_slot(event.get('open_layer_slot'))
            if slot['index']!=1:raise Unsupported('Exception layer result is not consumed by the actual DOM sink')
            if kind=='detail':result=opening;entry=opening
            else:
                result=steps['recheck'];entry=opening;layer=layers[0]
                if result['model']is not opening['model']or result['parent']is not opening['parent']or normalized_callee(event.get('callee'))!=(result['function'].symbol,):raise Unsupported('Exception DOM event is not bound to the exact original lexical recheck')
                bt,br=self.p4_execution_const(opening,'button');self.p4_execution_equal(br,"document.getElementById('call-data-recheck')")
                template=layer.args[1]
                if len(template)!=1 or template[0].kind!='template'or 'id="call-data-recheck"'not in template[0].value:raise Unsupported('Exception DOM listener has no original mounted button')
                self.p4_execution_pattern(opening,'if(button)button.onclick=()=>{if(token!==context())return showToast(\'工作范围已变化，请重新打开记录\',\'warning\');'+result['function'].symbol+'('+ident+');};',root=True)
                self.p4_no_write_contexts(opening['model'],{ident,'token'},opening['function'].body_start,opening['function'].body_end,[token])
                self.p4_execution_pattern(result,"const r=find("+ident+");if(!r||['核对中','已恢复'].includes(r.status))return;",root=True)
                self.p4_execution_pattern(result,'const token=context(),operator=AppState.account().name||\'超级管理员\';',root=True)
                # The candidate is the complete original local recheck handler.
                # Its delayed source-local branch is retained, not a real API.
                timer=self.p4_execution_call(result,'setTimeout',result['evidence'].record)
                if len(timer.args)!=2 or [t.value for t in timer.args[1]]!=['650']:raise Unsupported('Exception local recheck delay/actual callback differs')
                delayed=self.p4_execution_callback_step(result,timer.args[0])
                self.p4_execution_pattern(delayed,"if(token!==context()||!find("+ident+")){r.status='仍需处理';")
                self.p4_execution_pattern(delayed,'Object.assign(c,{result:response.result,endedAt:response.endedAt,durationSeconds:response.durationSeconds,updatedAt:new Date().toISOString()});')
                self.p4_execution_pattern(delayed,"CloudCallRuntime.addAudit('通话数据核对（演示）',r.callId,r.tenantId,'待核对',r.status);")
        terminal=self.p4_execution_named(flow.get('terminal_binding',flow.get('result_binding',{})))
        return {'entry_evidence':entry['evidence'],'terminal_evidence':terminal['evidence'],'terminal_symbol':terminal['function'].symbol,'result_evidence':result['evidence'],'result_symbol':result['function'].symbol,'followup_verified':False}

    def p4_execution_callback_step(self,parent,tokens):
        if len(tokens)<5 or [t.value for t in tokens[:4]]!=['(',')','=>','{']or tokens[-1].value!='}':raise Unsupported('Execution deferred edge is not the actual original zero-argument arrow callback')
        fn=parent['model'].enclosing_function(tokens[4].start)
        if fn is None or fn.parent is not parent['function']:raise Unsupported('Execution callback is detached from its original lexical capture')
        return {'function':fn,'model':parent['model'],'parent':fn.parent,'body':tokens[4:-1],'params':[],'evidence':parent['evidence']}

    def p4_captured_user_save_contract(self,flow,anchor_info,save_info,commit_info):
        follow=flow.get('result_binding',{}).get('followup')
        if not isinstance(follow,dict)or follow.get('version')!='bounded-captured-user-save@1':raise Unsupported('Original initiator needs explicit captured user-save source evidence')
        for label,actual in [('anchor',anchor_info),('save',save_info),('result',commit_info)]:
            declared=follow.get(label)
            if not isinstance(declared,dict)or not isinstance(actual,dict)or not isinstance(actual.get('evidence'),Evidence):raise Unsupported('Followup source endpoint is missing: '+label)
            e=self.evidence(declared.get('evidence'))
            if declared.get('symbol')!=actual['function'].symbol or e.model is not actual['model']or(e.start,e.end)!=(actual['evidence'].start,actual['evidence'].end)or not e.contains(actual['function']):raise Unsupported('Followup source endpoint differs from the actual original function: '+label)
        if save_info['function']is anchor_info['function']or commit_info['function']is anchor_info['function']:raise Unsupported('Opening an original form cannot count as its actual later user save/result')
        event=self.evidence(follow.get('save_event',{}).get('evidence'));actual_event=save_info.get('event_evidence')
        if not isinstance(actual_event,Evidence)or event.model is not actual_event.model or(event.start,event.end)!=(actual_event.start,actual_event.end):raise Unsupported('Followup save event is not the real independently checked control')
        captures=save_info.get('capture_proofs',{})
        if not isinstance(captures,dict)or not {'context','mode'}.issubset(captures)or not({'identity','selection_ids','new_object_scope'}&set(captures)):raise Unsupported('Followup requires actual context/mode and original identity/selection/new-object scope proof')
        for role,tokens in captures.items():
            if role not in {'context','mode','identity','selection_ids','new_object_scope','revision'}or not isinstance(tokens,list)or not tokens or any(not isinstance(t,Token)for t in tokens):raise Unsupported('Followup capture facts must come from parsed original source tokens')
            if not all(any(t is original for model in {anchor_info['model'],save_info['model']}for ts in model.lists for original in ts)for t in tokens):raise Unsupported('Followup capture proof is not an actual source token of the checked operation')
        call=commit_info.get('call');pub=commit_info.get('publication')
        if not isinstance(call,Call)or not any(call is actual for actual in save_info['model'].calls)or not(save_info['function'].body_start<call.start<call.end<save_info['function'].body_end):raise Unsupported('Followup commit is not an actual source call of the original user-save handler')
        if not isinstance(pub,dict)or pub.get('function')is not commit_info['function']or pub.get('model')is not commit_info['model']:raise Unsupported('Followup actual result lacks its exact published component')
        resolved=call.path
        factory=commit_info.get('factory')
        if factory is not None:
            if not isinstance(factory,dict)or resolved[0]!=factory.get('symbol','')+'()':raise Unsupported('Followup commit factory differs from its source receiver')
            resolved=factory['owner']+resolved[1:]
        alias=commit_info.get('receiver_alias')
        if alias is not None:
            ae=self.evidence(alias.get('evidence'))
            if ae.model is not save_info['model']or resolved[0]!=alias.get('alias'):raise Unsupported('Followup component alias is disconnected')
            self.p2_const_alias(ae,alias['alias'],alias.get('owner'),save_info['parent'],call.start)
            resolved=normalized_callee(alias['owner'])+resolved[1:]
        if resolved!=pub['canonical_callee']and ('window',)+resolved!=pub['canonical_callee']:raise Unsupported('Followup commit receiver does not resolve to its exact result implementation')
        # Mode/identity/context/revision semantics remain the responsibility of
        # the family decoder that produced the internal Token/Call proofs.
        return True

    def p4_execution_monitor_frame(self,spec):
        if not isinstance(spec,dict)or spec.get('version')!='bounded-monitor-workbench-embedding@1':raise Unsupported('Monitor flow needs its actual bounded workbench embedding')
        route=self.p4_flow_route(spec.get('route'));frame=self.p4_execution_named(spec.get('frame_function'));page=self.p4_flow_export(spec.get('page_publication'))
        monitor=self.p4_execution_named(spec.get('monitor_render_function'));pub=self.p4_flow_export(spec.get('monitor_publication'));body=self.p4_execution_named(spec.get('body_function'))
        if page['function']is not frame['function']or pub['function']is not monitor['function']or body['model']is not monitor['model']or body['parent']is not monitor['parent']:
            raise Unsupported('Monitor workbench is not the actual original published embedded owner')
        names=spec.get('names',{});required=['workspace_tab','supervisor','content','eligible','monitor_id']
        if not isinstance(names,dict)or any(not IDENT.fullmatch(names.get(x,''))for x in required):raise Unsupported('Monitor embedding state names are incomplete')
        tab=names['workspace_tab'];supervisor=names['supervisor'];content=names['content'];callee=spec.get('monitor_callee')
        context=route.get('context_proof');eligible=monitor['model'].declaration(names['eligible'],monitor['parent'])
        if not isinstance(context,dict)or context.get('guard_function')is not eligible or context.get('guard_model')is not monitor['model']:
            raise Unsupported('Monitor embedding lacks its own same-source eligible/policy route exclusion')
        sv,sr=self.p4_execution_const(frame,supervisor);self.p4_execution_equal(sr,'window.TenantCallMonitor?.'+names['eligible']+'()')
        self.no_shadow(monitor['model'],names['eligible'],monitor['parent'],[monitor['function'].body_start+1])
        if normalized_callee(callee)!=pub['canonical_callee']:raise Unsupported('Workbench embeds another monitor owner')
        self.p4_execution_pattern(frame,'if('+tab+"==='monitor'&&"+supervisor+')'+content+'='+callee+'();',root=True)
        ret=self.p4_execution_return(frame);markup,values=self.p4_execution_concat(ret);hp=TableHTMLContext(values);hp.feed(markup);hp.close()
        if not any([t.value for t in value]==[content]and marker in hp.active for marker,value in values.items()):raise Unsupported('Workbench does not return its selected monitor content in active HTML')
        mr=self.p4_execution_return(monitor);mm=matching(mr);colon=None;at=0
        if [t.value for t in mr[:4]]!=[names['eligible'],'(',')','?']:raise Unsupported('Original monitor does not preserve its eligibility-controlled returned body')
        at=4
        while at<len(mr):
            if mr[at].value in {'(', '[', '{'}:at=mm[at]+1;continue
            if mr[at].value==':':colon=at;break
            at+=1
        if colon is None or len(mr[colon+1:])!=1 or literal(mr[colon+1])!='':raise Unsupported('Monitor unavailable branch is not the original empty result')
        consequent=mr[4:colon];calls_found=[c for c in calls(consequent)if c.path==(body['function'].symbol,)and not any(c.args)]
        if len(calls_found)!=1:raise Unsupported('Monitor body is not the exact original zero-argument source consumer')
        self.p4_execution_active_term(consequent,calls_found[0])
        self.p4_flow_const_literal(spec.get('monitor_id_binding'),monitor['model'],monitor['parent'])
        self.p4_flow_ui_alias(spec.get('ui_alias_evidence'),spec.get('ui_alias'),body['parent'],body['function'].body_start+1)
        return {'frame':frame,'monitor':monitor,'body':body,'publication':pub,'route':route,'ui_alias':spec['ui_alias'],'entry_evidence':frame['evidence']}

    def p4_flow_context_false_ranges(self,spec,model,fn):
        # Explicit source-role exclusion only. This proves one synchronous
        # original resolver branch false; it grants no actor/data permission.
        if not isinstance(spec,dict)or spec.get('version')!='bounded-source-role-exclusion@1':
            raise Unsupported('Resolver exclusion needs its explicit finite source-role contract')
        if fn.symbol!=spec.get('resolver_symbol'):
            return []
        resolver=self.p4_execution_named(spec.get('resolver'))
        if resolver['model']is not model or resolver['function']is not fn or resolver['function'].symbol!=spec.get('resolver_symbol'):
            raise Unsupported('Resolver exclusion does not bind the actual selected resolver')
        selector=self.p4_execution_named(spec.get('selector'));guard=self.p4_execution_named(spec.get('guard'));policy=self.p4_execution_named(spec.get('policy'));access=self.p4_execution_named(spec.get('access'))
        if selector['model']is not model or selector['parent']is not fn.parent:
            raise Unsupported('Resolver selector is not the actual lexical selector')
        gp=self.p4_flow_export(spec['guard'].get('publication'));pp=self.p4_flow_export(spec['policy'].get('publication'));ap=self.p4_flow_export(spec['access'].get('publication'));sp=self.p4_flow_export(spec.get('state_publication'))
        for pub,actual in [(gp,guard),(pp,policy),(ap,access)]:
            if pub['model']is not actual['model']or pub['function']is not actual['function']:
                raise Unsupported('Source-role exclusion publication points at a different actual function')
        if ap['canonical_callee']!=('window','AppState','effectiveAccess')or sp['canonical_callee']!=('window','AppState','get')or sp['model']is not access['model']:
            raise Unsupported('Source-role exclusion borrows another state/access publication')
        if pp['canonical_callee']!=('window','TenantSupervisorPolicy','isTenantAdmin')or gp['canonical_callee']!=('window','TenantCallMonitor','eligible'):
            raise Unsupported('Source-role exclusion is not the actual original monitor/policy chain')
        required=spec.get('required_role');excluded=spec.get('excluded_role')
        if not isinstance(required,str)or not isinstance(excluded,str)or required==excluded or spec.get('role_field')!='roleCode':
            raise Unsupported('Source-role exclusion needs distinct source literal role comparisons')
        # The actual selector only returns a nonempty workbench for OPERATOR.
        self.p4_execution_equal(selector['body'],'if(AppState.get().activeDomain!==\'CLOUD_CONTACT_CENTER\'||AppState.effectiveAccess().roleCode!=='+repr(excluded)+')return \'\';return window.AgentWorkbench?.hasSeat()?\'seat-workbench\':\'home\';')
        self.no_shadow(model,'AppState',None,[selector['function'].start],include_parent=True)
        self.no_shadow(model,selector['function'].symbol,selector['parent'],[fn.body_start+1])
        # eligible() true implies this exact published policy result is true.
        if selector['params']!=[]or guard['params']!=[]or policy['params']!=[]:
            raise Unsupported('Source-role exclusion only binds original zero-argument guards')
        gt,gr=self.p4_execution_const(guard,'s');self.p4_execution_equal(gr,'state()')
        factories=[]
        for ts in guard['model'].lists:
            for i,t in enumerate(ts):
                part=ts[i:i+12]
                if [x.value for x in part]==['const','state','=','(',')','=>','root','.','AppState','.','get','(']and i+13<len(ts)and [x.value for x in ts[i+12:i+14]]==[')',';']and guard['model'].scope(t.start)is guard['parent']:factories.append(ts[i+1])
        if len(factories)!=1:raise Unsupported('Monitor current-state factory differs from the same AppState publication')
        self.p2_no_owner_writes(guard['model'],[('state',)],[factories[0]])
        self.no_shadow(guard['model'],'state',guard['parent'],[guard['function'].body_start+1])
        self.p4_no_write_contexts(guard['model'],{'s'},guard['function'].body_start,guard['function'].body_end,[gt])
        self.p2_state_mutators(guard['model'],{'s'},guard['function'].body_start,guard['function'].body_end,label='Resolver monitor state')
        ret=self.p4_execution_return(guard);pieces=split_tokens(ret,'&&')
        if self.p4_execution_tokens(guard['body'])!=self.p4_execution_tokens(lex('const s=state();return'))+self.p4_execution_tokens(ret)+self.p4_execution_tokens(lex(';')):
            raise Unsupported('Monitor role prerequisite can be bypassed by another original guard statement/return')
        if not pieces or self.p4_execution_tokens(pieces[0])!=self.p4_execution_tokens(lex('root.TenantSupervisorPolicy?.isTenantAdmin()===true')):
            raise Unsupported('Monitor eligibility no longer requires the actual published policy')
        self.p4_flow_root_alias(spec['guard'].get('root_binding'),guard['model'],guard['parent'])
        # Policy true must pass its ADMIN comparison and same AppState access.
        pt,pr=self.p4_execution_const(policy,'app');self.p4_execution_equal(pr,'root.AppState')
        st,sr=self.p4_execution_const(policy,'state');self.p4_execution_equal(sr,'app?.get?.()')
        ac,ar=self.p4_execution_const(policy,'access');self.p4_execution_equal(ar,'app?.effectiveAccess?.()')
        self.p4_execution_pattern(policy,'if(!state||!access?.valid||access.roleCode!=='+repr(required)+"||app?.isReady?.()===false||state.activeDomain!=='CLOUD_CONTACT_CENTER')return false;",root=True)
        self.p4_execution_pattern(policy,'if(access.accountId!==state.accountId||access.tenantId!==state.tenantId||access.enterpriseId!==state.enterpriseId)return false;',root=True)
        self.p4_execution_equal(self.p4_execution_return(policy),'powerFor({accountId:state.accountId,tenantId:state.tenantId,enterpriseId:state.enterpriseId})===1')
        self.p4_execution_equal(policy['body'],"const app=root.AppState,state=app?.get?.(),access=app?.effectiveAccess?.();if(!state||!access?.valid||access.roleCode!=="+repr(required)+"||app?.isReady?.()===false||state.activeDomain!=='CLOUD_CONTACT_CENTER')return false;if(access.accountId!==state.accountId||access.tenantId!==state.tenantId||access.enterpriseId!==state.enterpriseId)return false;return powerFor({accountId:state.accountId,tenantId:state.tenantId,enterpriseId:state.enterpriseId})===1;")
        self.p4_flow_root_alias(spec['policy'].get('root_binding'),policy['model'],policy['parent'])
        self.p4_no_write_contexts(policy['model'],{'app','state','access'},policy['function'].body_start,policy['function'].body_end,[pt,st,ac])
        self.p2_state_mutators(policy['model'],{'app','state','access'},policy['function'].body_start,policy['function'].body_end,label='Resolver source policy')
        # effectiveAccess's role is the same original current membership value;
        # it is not a role literal supplied by the proof caller.
        av=self.p4_execution_return(access)
        if not av or av[0].value!='{'or matching(av).get(0)!=len(av)-1:raise Unsupported('Actual access role is not a returned literal object')
        role_values=[p[2:]for p in split_tokens(av[1:-1])if len(p)>2 and [x.value for x in p[:2]]==['roleCode',':']]
        if len(role_values)!=1:raise Unsupported('Actual access role property is absent or duplicated')
        self.p4_execution_equal(role_values[0],"member?.roleCode||''")
        mt,mr=self.p4_execution_const(access,'member');self.p4_execution_equal(mr,'currentMembership()')
        self.p4_no_write_contexts(access['model'],{'member'},access['function'].body_start,access['function'].body_end,[mt])
        self.p2_state_mutators(access['model'],{'member'},access['function'].body_start,access['function'].body_end,label='Resolver source access')
        # Only the exact immediate selector binding and actual guarded rewrite
        # are excluded. The generic route validator checks every other write.
        wt,wr=self.p4_execution_const(resolver,'workbench');self.p4_execution_equal(wr,selector['function'].symbol+'()')
        branch=self.p4_execution_pattern(resolver,"if(workbench&&['home','seat-workbench'].includes(key)){key=workbench;route=routes[key];}",root=True)
        if wt.start>=branch[0].start:raise Unsupported('Resolver selector binding follows its selected rewrite')
        self.p4_no_write_contexts(model,{'workbench'},wt.end,fn.body_end)
        self.p2_state_mutators(model,{'workbench'},wt.end,fn.body_end,label='Resolver source selector')
        self._p4_verified_role_context={'guard_function':guard['function'],'guard_model':guard['model'],'guard_evidence':guard['evidence'],'policy_function':policy['function'],'policy_model':policy['model'],'access_function':access['function'],'access_model':access['model'],'required_role':required,'excluded_role':excluded,'resolver_function':fn,'resolver_model':model}
        return [(branch[0].start,branch[-1].end)]

    def p4_application_frame_state(self,page,names,local_tokens):
        view,selected=names['view'],names['selected_id'];options=names['options'];row=names['row'];body=page['body'];ms=matching(body);allowed=list(local_tokens);seen=set()
        guards={"contextChanged":view+"='list';"+selected+"='';", "!contextChanged&&!"+options+".preserveFilters&&!"+options+".applicationView":view+"='list';"+selected+"='';", "!contextChanged&&"+options+".applicationView":view+'=nextView;'+selected+'='+options+".applicationId||'';", "['detail','edit'].includes("+view+')&&!'+row:view+"='list';"+selected+"='';"}
        for i,t in enumerate(body[:-1]):
            if t.value!='if'or body[i+1].value!='('or page['model'].enclosing_function(t.start)is not page['function']:continue
            close=ms[i+1];cond=self.p4_execution_tokens(body[i+2:close]);found=[key for key in guards if self.p4_execution_tokens(lex(key))==cond]
            if not found:continue
            key=found[0]
            if key in seen or close+1>=len(body)or body[close+1].value!='{':raise Unsupported('Application original view guard is duplicated or structurally changed')
            seen.add(key);end=ms[close+1];branch=dict(page,body=body[close+2:end]);pair=self.p4_execution_pattern(branch,guards[key],root=True,within=(body[close+1].start,body[end].end))
            allowed.extend([pair[0],pair[4]])
        if seen!=set(guards):raise Unsupported('Application original view/id reset and selected-option guards are incomplete')
        self.p4_no_write_contexts(page['model'],{view,selected,names['list_result'],row,names['body']},page['function'].body_start,page['function'].body_end,allowed)
        self.p2_state_mutators(page['model'],{names['list_result'],row,names['body']},page['function'].body_start,page['function'].body_end,label='Application frame result')

    def p4_execution_ternary(self,tokens):
        tokens=self.p3_unwrap(tokens);ms=matching(tokens);q=None;depth=0;i=0
        while i<len(tokens):
            if tokens[i].value in {'(','[','{'}:i=ms[i]+1;continue
            if tokens[i].value=='?':
                if q is None:q=i
                else:depth+=1
            elif tokens[i].value==':'and q is not None:
                if depth:depth-=1
                else:return tokens[:q],tokens[q+1:i],tokens[i+1:]
            i+=1
        raise Unsupported('Execution conditional source has no exact selected ternary branches')

    def p4_execution_active_value(self,tokens,name):
        markup,values=self.p4_execution_concat(tokens);parser=TableHTMLContext(values);parser.feed(markup);parser.close()
        if len([x for marker,x in values.items()if [t.value for t in x]==[name]and marker in parser.active])!=1:
            raise Unsupported('Execution value is not a unique active original returned HTML term: '+name)

    def p4_execution_arrow_step(self,parent,tokens):
        ms=matching(tokens)
        if tokens and tokens[0].kind=='id'and len(tokens)>3 and tokens[1].value=='=>':params=[[tokens[0]]];at=2
        elif tokens and tokens[0].value=='(':
            close=ms[0];params=split_tokens(tokens[1:close]);params=[]if params==[[]]else params;at=close+2
            if tokens[close+1].value!='=>':raise Unsupported('Execution original callback is not an arrow')
        else:raise Unsupported('Execution original callback parameters are unsupported')
        if any(len(p)!=1 or p[0].kind!='id'for p in params)or tokens[at].value!='{'or ms[at]!=len(tokens)-1:raise Unsupported('Execution actual callback needs literal parameters and one original body')
        fn=parent['model'].enclosing_function(tokens[at+1].start)
        if fn is None or fn.parent is not parent['function']:raise Unsupported('Execution actual callback is disconnected from its original parent')
        return {'function':fn,'model':parent['model'],'parent':fn.parent,'body':tokens[at+1:-1],'params':params,'evidence':parent['evidence']}

    def p4_captured_monitor_flow(self,flow):
        op=flow['entry_binding'];kind=op.get('operation');event=flow['event_binding'];result_spec=flow['result_binding'];steps=self.p4_execution_steps(flow)
        if kind not in {'pause','unpause','logout'}:raise Unsupported('Original monitor operation is not a selected source management action')
        embedded=self.p4_execution_monitor_frame(op.get('monitor_frame'));body=embedded['body'];row_actions=steps['row_actions'];opening=steps['opening'];draw=steps['draw'];save=steps['save'];result=self.p4_execution_named(result_spec)
        if any(x['model']is not body['model']or x['parent']is not body['parent']for x in [row_actions,opening,draw,save]):raise Unsupported('Monitor actions/captured user save are not the original embedded component functions')
        for role,actual in [('opening',opening),('save',save)]:
            bound=self.p4_flow_export(event.get(role+'_publication'))
            if bound['function']is not actual['function']or bound['model']is not actual['model']:raise Unsupported('Monitor event export differs from the actual original '+role)
        # Prove the conditional current snapshot's actual table and cno row.
        _,overview=self.p4_execution_const(body,'overview');self.p4_execution_active_value(overview,'live')
        returned=self.p4_execution_return(body);html,values=self.p4_execution_concat(returned);hp=TableHTMLContext(values);hp.feed(html);hp.close()
        nodes=[v for marker,v in values.items()if marker in hp.active and self.p4_execution_tokens(self.p3_unwrap(v))==self.p4_execution_tokens(lex("monitorTab==='events'?eventLog():overview"))]
        if len(nodes)!=1:raise Unsupported('Monitor overview is not the actual active original body branch')
        _,live=self.p4_execution_const(body,'live');condition,yes,no=self.p4_execution_ternary(live);self.p4_execution_equal(condition,'current||read')
        tables=[c for c in calls(yes)if c.path==('ui','table')]
        if len(tables)!=1 or len(tables[0].args)!=2:raise Unsupported('Monitor current rows have no unique original table')
        table=tables[0];self.p4_execution_active_term(yes,table);self.p4_execution_equal(table.args[1],'seats')
        columns=table.args[0];items=split_tokens(columns[1:-1]);spread=[p for p in items if p and p[0].value=='...']
        if len(spread)!=1:raise Unsupported('Monitor management column is not the original conditional current-snapshot spread')
        sc,sy,sn=self.p4_execution_ternary(spread[0][1:]);self.p4_execution_equal(sc,'current');self.p4_execution_equal(sn,'[]')
        extra=split_tokens(sy[1:-1]);selected=[self.p1_props(c)for c in extra if self.p1_literal(self.p1_props(c).get('key',[]))=='cno']
        if len(selected)!=1:raise Unsupported('Monitor selected management column no longer identifies the same cno')
        self.p4_execution_equal(selected[0].get('render',[]),'(_,row)=>'+row_actions['function'].symbol+'(row)')
        self.p1_table_contract(self.evidence(event.get('table_implementation_evidence')),self.evidence(event.get('table_export_evidence')),'cno')
        _,monitored=self.p4_execution_const(body,'monitored');self.p4_execution_equal(monitored,'current?monitoredSeats(current.result):read?.rows||[]')
        _,seats=self.p4_execution_const(body,'seats');self.p4_execution_equal(seats,'ui.sortByUpdated?.(monitored)||monitored')
        declared=[self.p4_execution_const(body,n)[0]for n in ['current','read','monitored','seats','live','overview']]
        self.p4_no_write_contexts(body['model'],{'current','read','monitored','seats','live','overview'},body['function'].body_start,body['function'].body_end,declared)
        # The selected action comes only from the original row state selector
        # and the literal logout sibling, not a caller supplied capability.
        rp=[p[0].value for p in row_actions['params']]
        if rp!=['row']:raise Unsupported('Monitor management renderer does not consume its actual row')
        self.p4_execution_pattern(row_actions,"if(row.cno===agent()?.cno)return",root=True)
        self.p4_execution_pattern(row_actions,"if(['离线','未上线','下线'].includes(row.state))return",root=True)
        self.p4_execution_pattern(row_actions,"if(!['空闲','置忙','示忙','暂停接听','可接听'].includes(row.state))return",root=True)
        act,ar=self.p4_execution_const(row_actions,'action');self.p4_execution_equal(ar,"['空闲','可接听'].includes(row.state)?'pause':'unpause'")
        rt=self.p4_execution_return(row_actions);rh,rv=self.p4_execution_concat(rt);rp=TableHTMLContext(rv);rp.feed(rh);rp.close()
        map_parts=[v for marker,v in rv.items()if marker in rp.active and len(v)>8 and self.p4_execution_tokens(v[:8])==self.p4_execution_tokens(lex("[action,'logout'].map("))]
        if len(map_parts)!=1:raise Unsupported('Monitor literal action buttons do not use their actual active map callback')
        mapped=map_parts[0];mc=matching(mapped)[7]
        self.p4_execution_equal(mapped[mc+1:],".join('')")
        cb=self.p4_execution_arrow_step(row_actions,mapped[8:mc])
        if [p[0].value for p in cb['params']]!=['value']:raise Unsupported('Monitor button callback action is disconnected')
        self.p4_execution_pattern(cb,"const permission=service().managementEligibility?.(agent(),row.cno,value)||{ok:false,message:'暂时无法调整'};",root=True)
        self.p4_execution_pattern(cb,'const disabled=loading||managementBusy()||management().pending||!permission.ok;',root=True)
        args=self.p4_execution_html_event(self.p4_execution_return(cb),'click',event.get('opening_callee'))
        if len(args)!=2:raise Unsupported('Monitor opening event changed its original action/cno slots')
        self.p4_execution_equal(args[0],'value');self.p4_execution_equal(args[1],'esc(row.cno)')
        self.p4_no_write_contexts(row_actions['model'],{'row','action'},row_actions['function'].body_start,row_actions['function'].body_end,[act])
        self.p4_no_write_contexts(cb['model'],{'value'},cb['function'].body_start,cb['function'].body_end)
        self.p2_state_mutators(row_actions['model'],{'row'},row_actions['function'].body_start,row_actions['function'].body_end,label='Monitor target identity')
        self.p2_protected_escapes(row_actions['model'],{'row'},row_actions['function'].body_start,row_actions['function'].body_end,label='Monitor target identity')
        # Re-read same target cno/scope and eligibility before opening/save.
        self.p4_execution_pattern(opening,'if(!labels[action]||!eligible()||!onlineSession()||loading||managementBusy()||management().pending)return false;',root=True)
        self.p4_execution_pattern(opening,'const row=snapshot?.scope===activeScope?monitoredSeats(snapshot.result).find(row=>row.cno===cno):null;',root=True)
        self.p4_execution_pattern(opening,'const permission=row&&service().managementEligibility?.(agent(),cno,action);',root=True)
        capture=self.p4_execution_pattern(opening,"manageView={...row,action,scope:activeScope,message:''};"+draw['function'].symbol+'();return true;',root=True)
        row_decl,row_rhs=self.p4_execution_const(opening,'row')
        self.p4_no_write_contexts(opening['model'],{'action','cno','manageView'},opening['function'].body_start,opening['function'].body_end,[capture[0]])
        self.p4_no_write_contexts(opening['model'],{'row'},row_rhs[-1].end,opening['function'].body_end)
        dv,dr=self.p4_execution_const(draw,'v');self.p4_execution_equal(dr,'manageView')
        self.p4_no_write_contexts(draw['model'],{'v'},draw['function'].body_start,draw['function'].body_end,[dv])
        self.p2_state_mutators(draw['model'],{'v'},draw['function'].body_start,draw['function'].body_end,label='Monitor confirmation identity')
        self.p2_protected_escapes(draw['model'],{'v'},draw['function'].body_start,draw['function'].body_end,[dv],label='Monitor confirmation identity')
        layer=self.p4_execution_call(draw,'ui.openLayer',event.get('layer_evidence'));slot=self.p4_flow_ui_slot(event.get('open_layer_slot'))
        if slot['index']!=1 or len(layer.args)!=3:raise Unsupported('Monitor save control is not consumed by the actual original active layer slot')
        if self.p4_execution_html_event(layer.args[1],'click',event.get('save_callee')):raise Unsupported('Monitor save button unexpectedly takes arguments')
        target,tr=self.p4_execution_const(save,'target');self.p4_execution_equal(tr,'manageView')
        self.p4_execution_pattern(save,'if(!target||!onlineSession()||managementBusy()||management().pending||target.scope!==scope()||!eligible())return {ok:false};',root=True)
        self.p4_execution_pattern(save,'const permission=service().managementEligibility?.(agent(),target.cno,target.action);',root=True)
        self.p4_execution_pattern(save,'if(!permission?.ok){')
        commit=self.p4_execution_call(save,'service().manageSeat',save['evidence'].record)
        if len(commit.args)!=2:raise Unsupported('Monitor commit changed its original action/target slots')
        self.p4_execution_equal(commit.args[0],'agent()');self.p4_execution_equal(commit.args[1],'{action:target.action,cno:target.cno}')
        self.p4_execution_pattern(save,'try{result=await service().manageSeat(agent(),{action:target.action,cno:target.cno});}',root=True)
        self.p4_execution_pattern(save,'if(target.scope!==scope()||!eligible()){closeManage(true);resetScope();refresh();return result;}',root=True)
        self.p4_execution_pattern(save,'if(result.ok){');self.p4_execution_pattern(save,'else if(result.pending||management().pending){')
        allowed_target=[target]+[self.p4_execution_pattern(save,text)[0]for text in ["target.message=permission?.message||'当前无法执行该操作';","target.message='';","target.message=result.message||'操作未完成，原状态保留';"]]
        self.p4_no_write_contexts(save['model'],{'target'},save['function'].body_start,save['function'].body_end,allowed_target)
        self.p2_state_mutators(save['model'],{'target'},save['function'].body_start,save['function'].body_end,label='Monitor saved target')
        self.p2_protected_escapes(save['model'],{'target'},save['function'].body_start,save['function'].body_end,[target],label='Monitor saved target')
        success_result=self.p4_execution_pattern(save,'result=await service().manageSeat(agent(),{action:target.action,cno:target.cno});')[0]
        failure_result=self.p4_execution_pattern(save,"result={ok:false,message:'操作未完成，请刷新监控核对当前状态'};")[0]
        self.p4_no_write_contexts(save['model'],{'result'},save['function'].body_start,save['function'].body_end,[success_result,failure_result])
        self.p2_state_mutators(save['model'],{'result'},save['function'].body_start,save['function'].body_end,label='Monitor management result')
        publication=self.p4_flow_export(result_spec.get('publication'));factory=self.p4_flow_factory(op.get('service_factory'))
        if publication['function']is not result['function']or factory['owner']+('manageSeat',)!=publication['canonical_callee']:raise Unsupported('Monitor commit does not resolve to the actual original published local result')
        self.p4_execution_pattern(result,"if(!object(input)||Object.keys(input).some(key=>!['action','cno'].includes(key)))return fail('坐席管理参数无效');",root=True)
        self.p4_execution_pattern(result,'const {action,cno}=input,eligible=managementEligibility(agent,cno,action);if(!eligible.ok)return eligible;',root=True)
        self.p4_execution_pattern(result,'if(generation!==token||captured!==contextKey()||!currentTarget.ok||currentTarget.target.contactCenterIdentityId!==expectedIdentity){',root=True)
        self.p4_execution_pattern(result,"const permitted={pause:['空闲'],unpause:['置忙'],logout:['空闲','置忙']}[action];",root=True)
        self.p4_execution_pattern(result,"if(scenario==='unknown'){")
        self.p4_execution_equal(self.p4_execution_return(result),'{ok:succeeded,message:managementMessage,request,response}')
        terminal=self.p4_execution_named(flow.get('terminal_binding',result_spec))
        return {'entry_evidence':save['evidence'],'terminal_evidence':terminal['evidence'],'terminal_symbol':terminal['function'].symbol,'result_evidence':result['evidence'],'result_symbol':result['function'].symbol,'followup_verified':False}

    def p4_retry_user_save_flow(self,flow):
        op=flow['entry_binding'];event=flow['event_binding'];steps=self.p4_execution_steps(flow);render=steps['render'];producer=steps['producer'];wrapper=steps['wrapper'];anchor=steps['anchor'];opening=steps['opening'];save=steps['save'];guard=steps['correct_guard'];result=self.p4_execution_named(flow['result_binding'])
        route=self.p4_flow_route(op.get('route'));page=self.p4_flow_export(op.get('page_publication'))
        if page['function']is not render['function']or any(s['model']is not render['model']or s['parent']is not render['parent']for s in [producer,wrapper,anchor,opening,save,guard]):raise Unsupported('Retry original route/producer/anchor/save functions do not belong to the same published page')
        if route['route_view']!='sync':raise Unsupported('Retry source must retain the original selected sync-records view')
        self.p4_execution_pattern(render,"view=options?.view||view;")
        self.p4_execution_pattern(render,"if(view==='sync')return "+producer['function'].symbol+'();',root=True)
        self.p4_flow_ui_alias(op.get('ui_alias_evidence'),'ui',render['parent'],producer['function'].body_start+1)
        returned=self.p4_execution_return(producer)
        if len(returned)!=1 or returned[0].kind!='template':raise Unsupported('Retry original list is not one literal returned page template')
        shell_calls=[]
        for kind,part in returned[0].parts:
            if kind!='expression'or not self.p1_active_part(returned[0],part):continue
            for c in calls(part):
                if c.path==(wrapper['function'].symbol,)and c.index==0 and c.end_index==len(part)-1:shell_calls.append(c)
        if len(shell_calls)!=1:raise Unsupported('Retry original list does not actually return its table wrapper')
        shell=shell_calls[0]
        if len(shell.args)!=5:raise Unsupported('Retry actual wrapper argument count differs')
        self.p4_execution_equal(shell.args[0],"'sync'");self.p4_execution_equal(shell.args[1],'rows')
        cols=shell.args[2]
        if not cols or cols[0].value!='['or matching(cols).get(0)!=len(cols)-1:raise Unsupported('Retry original operation column is not a literal table column')
        columns=[self.p1_props(p)for p in split_tokens(cols[1:-1])if p];selected=[p for p in columns if self.p1_literal(p.get('key',[]))=='syncId']
        if len(selected)!=1:raise Unsupported('Retry control lost its original same-record syncId key')
        renderer=selected[0].get('render',[])
        if len(renderer)!=7 or [t.value for t in renderer[:6]]!=['(','v',',','r',')','=>']or renderer[6].kind!='template'or len(renderer)!=7:raise Unsupported('Retry original table renderer is not the exact literal v/r returned template')
        template=renderer[6];parts=[p for typ,p in template.parts if typ=='expression'];matches=[]
        for part in parts:
            try:c,y,n=self.p4_execution_ternary(part);self.p4_execution_equal(c,guard['function'].symbol+'(r)');self.p4_execution_equal(n,"''")
            except Unsupported:continue
            if not self.p1_active_part(template,part):raise Unsupported('Retry conditional button is in an inert/attribute HTML slot')
            matches.append(y)
        if len(matches)!=1 or len(matches[0])!=1 or matches[0][0].kind!='template':raise Unsupported('Retry button is not the real mayCorrectRecord-selected child template')
        helper=self.p2_escaped_helper(event.get('handler_helper'),producer['parent'],template.start,escape_use_validator=lambda name:self.p4_escape_callback_uses(event.get('handler_helper'),producer['evidence'],name,producer['parent']))
        if helper['owner']+(helper['method'],)!=normalized_callee(event.get('opening_callee')):raise Unsupported('Retry original fixed helper owner/method differs from its callable anchor')
        args=self.p4_execution_html_event(matches[0],'click',event.get('opening_callee'),{'name':helper['name'],'owner':helper['owner']})
        if len(args)!=2:raise Unsupported('Retry original record/context event argument slots differ')
        self.p4_execution_equal(args[0],'v');self.p4_execution_equal(args[1],'key')
        self.p4_no_write_contexts(producer['model'],{'v','r'},renderer[0].start,renderer[-1].end)
        # Wrapper consumes the original columns and a nonempty conditional
        # source page slice; the real UI library invokes column(row[key],row).
        table=[c for c in wrapper['model'].calls if c.path==('ui','table')and wrapper['model'].enclosing_function(c.start)is wrapper['function']]
        if len(table)!=1:raise Unsupported('Retry wrapper has no unique original real table')
        self.p4_execution_equal(table[0].args[0],'columns');self.p4_execution_equal(table[0].args[1],'rows.slice(start,start+pageSize)')
        self.p4_execution_active_term([next(t for t in self.p4_execution_return(wrapper)if t.kind=='template')],table[0])
        self.p4_no_write_contexts(wrapper['model'],{'columns'},wrapper['function'].body_start,wrapper['function'].body_end)
        self.p1_table_contract(self.evidence(event.get('table_implementation_evidence')),self.evidence(event.get('table_export_evidence')),'syncId')
        # All three independently callable controls are bound to their actual
        # published owner; opening itself never counts as the saved result.
        for role,actual in [('anchor',anchor),('opening',opening),('save',save)]:
            pub=self.p4_flow_export(event.get(role+'_publication'))
            if pub['function']is not actual['function']:raise Unsupported('Retry '+role+' resolves to a different actual owner implementation')
        self.p4_execution_equal(anchor['body'],'if(key&&key!==contextKey())return;const row=CloudCallData.syncRecords.find(x=>x.syncId===id);if(!mayReadRecord(row)||!mayCorrectRecord(row))return;'+opening['function'].symbol+'(id,contextKey());')
        self.p4_execution_equal(guard['body'],"return !!row&&mayManage(row)&&row.objectType==='云呼叫坐席创建'&&row.status==='失败'&&!/超时|未知|未确认/.test(row.latestResult||row.failureReason||'');")
        self.p4_execution_pattern(opening,'if(key&&key!==contextKey())return;',root=True)
        self.p4_execution_pattern(opening,'const failed=CloudCallData.syncRecords.find(r=>r.syncId===syncId);',root=True)
        self.p4_execution_pattern(opening,'if(syncId&&!mayCorrectRecord(failed))return;',root=True)
        identity=self.p4_execution_pattern(opening,"retrySyncId=failed?.syncId||'';",root=True)
        context=self.p4_execution_pattern(opening,'createContext=contextKey();createRequestId=crypto.randomUUID();creating=false;')
        self.p4_execution_pattern(opening,"const tenants=scoped(CloudCallData.tenants).filter(t=>(!failed||t.tenantId===failed.tenantId)&&mayManage(t)&&t.status==='启用'&&t.capabilitySet.includes('CLOUD_CONTACT_CENTER'));",root=True)
        self.p4_execution_pattern(opening,'if(!tenants.length)return;',root=True)
        layer=self.p4_execution_call(opening,'ui.openLayer',event.get('layer_evidence'));self.p4_flow_ui_slot(event.get('open_layer_slot'))
        if len(layer.args)!=3 or literal(layer.args[0][0])!='agent-single':raise Unsupported('Retry actual input layer no longer captures its original same-record editor')
        if self.p4_execution_html_event(layer.args[1],'click',event.get('save_callee')):raise Unsupported('Retry save control has unexpected arguments')
        self.p4_execution_pattern(save,'if(creating)return;',root=True)
        self.p4_execution_pattern(save,"if(createContext!==contextKey()||!tenant||!mayManage(tenant)||tenant.status!=='启用'||!tenant.capabilitySet.includes('CLOUD_CONTACT_CENTER'))return",root=True)
        self.p4_execution_pattern(save,'const failed=CloudCallData.syncRecords.find(r=>r.syncId===retrySyncId);',root=True)
        self.p4_execution_pattern(save,'if(retrySyncId&&(!mayCorrectRecord(failed)||failed.tenantId!==tenant.tenantId))return;',root=True)
        submitted=self.p4_execution_pattern(save,'const submittedContext=createContext;creating=true;',root=True)
        commit=self.p4_execution_call(save,'AccountSeat.create',save['evidence'].record)
        self.p4_execution_equal(commit.args[0],'{tenantId:tenant.tenantId,userName,cno,areaCode}')
        self.p4_execution_equal(commit.args[1],'{requestId:createRequestId,configurationRecordId:retrySyncId||undefined}')
        self.p4_execution_pattern(save,'const result=await AccountSeat.create({tenantId:tenant.tenantId,userName,cno,areaCode},{requestId:createRequestId,configurationRecordId:retrySyncId||undefined});',root=True)
        self.p4_no_write_contexts(save['model'],{'retrySyncId','createContext','createRequestId'},save['function'].body_start,commit.start)
        result_token,_=self.p4_execution_const(save,'result')
        self.p4_no_write_contexts(save['model'],{'result'},save['function'].body_start,save['function'].body_end,[result_token])
        self.p2_state_mutators(save['model'],{'result'},save['function'].body_start,save['function'].body_end,label='Retry actual result')
        self.p2_protected_escapes(save['model'],{'result'},save['function'].body_start,save['function'].body_end,[result_token],label='Retry actual result')
        self.p4_execution_pattern(save,'creating=false;if(submittedContext!==contextKey())return;',root=True)
        self.p4_execution_pattern(save,'if(!result.ok){')
        publication=self.p4_flow_export(flow['result_binding'].get('publication'))
        if publication['function']is not result['function']or publication['canonical_callee']!=('window','AccountSeat','create'):raise Unsupported('Retry actual commit component differs from its source result')
        self.no_shadow(save['model'],'AccountSeat',None,[commit.start],include_parent=True)
        self.p4_execution_pattern(result,'if(options.configurationRecordId){',root=True)
        self.p4_execution_pattern(result,"if(accountId||!source||source.objectType!=='云呼叫坐席创建'||source.status!=='失败'||/超时|未知|未确认/.test(source.latestResult||source.failureReason||''))return")
        self.p4_execution_pattern(result,'request.configurationRecordId=source.syncId;request.operator=AppState.account().name;')
        locked=self.p4_execution_call(result,'locked',result['evidence'].record)
        if len(locked.args)!=2 or literal(locked.args[0][0])!='':raise Unsupported('Retry original local create lock/capture differs')
        commit_body=self.p4_execution_callback_step(result,locked.args[1])
        self.p4_execution_pattern(commit_body,"if(context!==stateKey())return {ok:false,message:'工作范围已变化，请重新打开新增页面'};",root=True)
        self.p4_execution_pattern(commit_body,'putSeat(saved,agent);putAttempt(saved,')
        self.p4_execution_pattern(commit_body,'write(saved);restore(saved);',root=True)
        # Exact local async result/body is carried as evidence, including locked
        # same-context, original failure preservation and subsequent skills UI.
        save['event_evidence']=opening['evidence'];save['capture_proofs']={'identity':identity,'context':context,'mode':self.p4_execution_pattern(guard,"row.objectType==='云呼叫坐席创建'"),'revision':submitted}
        result.update(call=commit,publication=publication,receiver_alias=None)
        verified=self.p4_captured_user_save_contract(flow,anchor,save,result)
        return {'entry_evidence':anchor['evidence'],'terminal_evidence':anchor['evidence'],'terminal_symbol':anchor['function'].symbol,'result_evidence':result['evidence'],'result_symbol':result['function'].symbol,'followup_verified':verified}

    def p4_outbound_group_user_save_flow(self,flow):
        op=flow['entry_binding'];mode=op.get('operation');event=flow['event_binding'];steps=self.p4_execution_steps(flow);render=steps['render'];anchor=steps['anchor'];save=steps['save'];transaction=steps['transaction'];result=self.p4_execution_named(flow['result_binding'])
        if mode not in {'create','edit'}:raise Unsupported('Original outbound user-save has an unsupported selected mode')
        route=self.p4_flow_route(op.get('route'));pub=self.p4_flow_export(op.get('page_publication'))
        if pub['function']is not render['function']or any(x['model']is not render['model']or x['parent']is not render['parent']for x in [anchor,save]):raise Unsupported('Outbound group original render/open/save page bindings differ')
        self.p4_flow_ui_alias(op.get('ui_alias_evidence'),'ui',render['parent'],render['function'].body_start+1)
        self.p4_execution_pattern(render,"if(!api.canAccess())return ui.empty('当前账号没有外呼组管理权限');",root=True)
        path=self.p4_execution_ui_path(render,event.get('opening_ui_path'));args=[]
        if mode=='edit':
            if path['row_parameters']!=['id','g']or path['column_key']!='outboundGroupId':raise Unsupported('Outbound edit event changed its original group identity column/slots')
            helper=self.p2_escaped_helper(event.get('handler_helper'),render['parent'],path['tokens'][0].start)
            if helper['owner']+(helper['method'],)!=normalized_callee(event.get('opening_callee')):raise Unsupported('Outbound edit event helper names another original owner/method')
            args=self.p4_execution_html_event(path['tokens'],'click',event.get('opening_callee'),{'name':helper['name'],'owner':helper['owner']})
            if len(args)!=2:raise Unsupported('Outbound edit opening no longer carries original object and context')
            self.p4_execution_equal(args[0],'id');self.p4_execution_equal(args[1],'listContext')
        else:
            if self.p4_execution_html_event(path['tokens'],'click',event.get('opening_callee')):raise Unsupported('Outbound create entry unexpectedly carries another object')
        for role,actual in [('anchor',anchor),('save',save)]:
            bound=self.p4_flow_export(event.get(role+'_publication'))
            if bound['function']is not actual['function']:raise Unsupported('Outbound '+role+' publication points at another handler')
        api_alias=self.evidence(op.get('api_alias_evidence'));self.p2_const_alias(api_alias,'api','OutboundGroups',save['parent'],save['function'].body_start+1)
        if mode=='create':
            self.p4_execution_pattern(anchor,'if(!valid(listContext))return denied();',root=True)
            new_scope=self.p4_execution_pattern(anchor,"if(tenants.length!==1)return showToast('当前账号须绑定唯一的有效租户','warning');",root=True)
            capture=self.p4_execution_pattern(anchor,"editor={kind:'create',context:api.contextKey(),revision:api.revision()};",root=True)
        else:
            self.p4_execution_pattern(anchor,'if(!valid(key))return denied();',root=True)
            self.p4_execution_pattern(anchor,'const g=api.get(id);if(!g)return denied();',root=True)
            capture=self.p4_execution_pattern(anchor,"editor={kind:'edit',id,context:api.contextKey(),revision:api.revision()};",root=True)
        self.p4_no_write_contexts(anchor['model'],{'editor'},anchor['function'].body_start,anchor['function'].body_end,[capture[0]])
        self.p2_state_mutators(anchor['model'],{'editor'},capture[-1].end,anchor['function'].body_end,label='Outbound original editor')
        self.p2_protected_escapes(anchor['model'],{'editor'},capture[-1].end,anchor['function'].body_end,label='Outbound original editor')
        layer=self.p4_execution_call(anchor,'ui.openLayer',event.get('layer_evidence'));slot=self.p4_flow_ui_slot(event.get('open_layer_slot'))
        if slot['index']!=1 or len(layer.args)!=3 or literal(layer.args[0][0])!='outbound-group-edit':raise Unsupported('Outbound independent save is not in the original active editor layer')
        if self.p4_execution_html_event(layer.args[1],'click',event.get('save_callee')):raise Unsupported('Outbound independent save has unexpected arguments')
        ec,er=self.p4_execution_const(save,'e');self.p4_execution_equal(er,'editor')
        self.p4_execution_pattern(save,'if(!e||!valid(e.context))return denied();',root=True)
        # Decode exactly the original mutually exclusive create/edit/member
        # branches. Their input/context/revision slots stay source-bound.
        body=save['body'];ms=matching(body);branches=[]
        for i,t in enumerate(body[:-1]):
            if t.value!='if'or body[i+1].value!='(':continue
            close=ms[i+1];cond=self.p4_execution_tokens(body[i+2:close])
            for value in ['create','edit']:
                if cond==self.p4_execution_tokens(lex('e.kind==='+repr(value))):
                    if body[close+1].value!='{':raise Unsupported('Outbound selected branch body changed')
                    branches.append((value,body[close+1].start,body[ms[close+1]].end,body[close+2:ms[close+1]]))
        if [x[0]for x in branches]!=['create','edit']:raise Unsupported('Outbound original create/edit branch order is missing or duplicated')
        selected=next(x for x in branches if x[0]==mode);calls_selected=[c for c in save['model'].calls if c.path==('api','create'if mode=='create'else'update')and selected[1]<c.start<c.end<selected[2]]
        if len(calls_selected)!=1:raise Unsupported('Outbound selected user-save does not invoke its exact original mode result')
        commit=calls_selected[0]
        if mode=='create':
            if len(commit.args)!=3:raise Unsupported('Outbound create lost its captured context/revision slots')
            self.p4_execution_equal(commit.args[1],'e.context');self.p4_execution_equal(commit.args[2],'e.revision')
            props=self.p1_props(commit.args[0]);self.p4_execution_equal(props.get('tenantId',[]),'tenantVal')
        else:
            if len(commit.args)!=4:raise Unsupported('Outbound edit lost its identity/context/revision slots')
            self.p4_execution_equal(commit.args[0],'e.id');self.p4_execution_equal(commit.args[2],'e.context');self.p4_execution_equal(commit.args[3],'e.revision')
        self.p4_execution_pattern(save,"if(!result.ok)return showToast(result.message,'warning');",root=True)
        cleanup=self.p4_execution_pattern(save,'editor=null;',root=True)
        assignments=[]
        for ts in save['model'].lists:
            for i,t in enumerate(ts[:-3]):
                if t.value=='result'and ts[i+1].value=='='and save['model'].enclosing_function(t.start)is save['function']:
                    cs=[c for c in save['model'].calls if c.start==ts[i+2].start and c.path in {('api','create'),('api','update'),('api','setMembers')}]
                    if len(cs)!=1:raise Unsupported('Outbound save overwrites its selected local result')
                    assignments.append(t)
        if len(assignments)!=3:raise Unsupported('Outbound save must retain the original three exclusive result assignments')
        self.p4_no_write_contexts(save['model'],{'e','result','editor'},save['function'].body_start,save['function'].body_end,[ec,cleanup[0]]+assignments)
        self.p2_state_mutators(save['model'],{'e','result','editor'},save['function'].body_start,save['function'].body_end,label='Outbound selected user-save state/result')
        result_decl=self.p4_execution_pattern(save,'let result;',root=True)[1]
        self.p2_protected_escapes(save['model'],{'e','result'},save['function'].body_start,save['function'].body_end,[ec,result_decl],label='Outbound selected user-save state/result')
        publication=self.p4_flow_export(flow['result_binding'].get('publication'))
        if publication['function']is not result['function']or transaction['model']is not result['model']or transaction['parent']is not result['parent']:raise Unsupported('Outbound actual result is not its original component transaction')
        transaction_call=self.p4_execution_call(result,transaction['function'].symbol,result['evidence'].record)
        if len(transaction_call.args)!=3:raise Unsupported('Outbound original transaction argument count differs')
        self.p4_execution_equal(transaction_call.args[0],'expectedContext');self.p4_execution_equal(transaction_call.args[1],'expectedRevision')
        callback=self.p4_execution_arrow_step(result,transaction_call.args[2])
        if mode=='create':self.p4_execution_pattern(callback,'next.groups.push(group);',root=True)
        else:self.p4_execution_pattern(callback,'const group=next.groups.find(g=>(g.outboundGroupId===outboundGroupIdOrGno||g.gno===outboundGroupIdOrGno)&&inScope(g));',root=True)
        self.p4_execution_pattern(transaction,"if(!canAccess())return failure('仅超级管理员或本租户管理员可维护当前范围的外呼组');",root=True)
        self.p4_execution_pattern(transaction,'if(expectedRevision!==undefined&&(!Number.isSafeInteger(expectedRevision)||expectedRevision!==latest.value.revision||snapshot.revision!==latest.value.revision)){',root=True)
        nt,nr=self.p4_execution_const(transaction,'next');self.p4_execution_equal(nr,'clone(latest.value)')
        self.p4_execution_pattern(transaction,"if(expectedContext&&contextKey()!==expectedContext)return failure('登录或租户范围已变化，请重新打开外呼组配置');",root=True,within=(transaction['function'].body_start,nt.start))
        self.p4_execution_pattern(transaction,"localStorage.setItem(storageKey,JSON.stringify(next));")
        self.p4_execution_pattern(transaction,"snapshot=next;rawSeen=JSON.stringify(next);errorMessage='';",root=True)
        save['event_evidence']=anchor['evidence'];save['capture_proofs']={'mode':capture,'context':capture,'revision':capture,('new_object_scope'if mode=='create'else'identity'):new_scope if mode=='create'else capture}
        result.update(call=commit,publication=publication,receiver_alias={'alias':'api','owner':'OutboundGroups','evidence':op.get('api_alias_evidence')})
        verified=self.p4_captured_user_save_contract(flow,anchor,save,result)
        return {'entry_evidence':anchor['evidence'],'terminal_evidence':anchor['evidence'],'terminal_symbol':anchor['function'].symbol,'result_evidence':result['evidence'],'result_symbol':result['function'].symbol,'followup_verified':verified}

    # Original reusable-field source flow, explicitly selected from the category
    # module's real field tab. No standalone field page or new API is invented.
    def p4_field_root_pattern(self,model,parent,evidence,source):
        e=self.evidence(evidence);wanted=self.p4_execution_tokens(lex(source));hits=[]
        if e.model is not model:raise Unsupported('Field module evidence is disconnected')
        for ts in model.lists:
            for i,t in enumerate(ts):
                part=ts[i:i+len(wanted)]
                if self.p4_execution_tokens(part)==wanted and e.start<=part[0].start and part[-1].end<=e.end and model.scope(part[0].start)is parent:hits.append(part)
        if len(hits)!=1:raise Unsupported('Field module original publication/state pattern is absent or duplicated')
        return hits[0]

    def p4_field_selected_child(self,template,condition,selected,expression_index=None):
        matches=[]
        expressions=[p for k,p in template.parts if k=='expression']
        if expression_index is not None:
            if type(expression_index)is not int or not 0<=expression_index<len(expressions):raise Unsupported('Field selected conditional expression index differs')
            expressions=[expressions[expression_index]]
        for part in expressions:
            if not self.p1_active_part(template,part):
                if expression_index is not None:raise Unsupported('Field selected conditional is in an inactive HTML slot')
                continue
            try:c,y,n=self.p4_execution_ternary(part);self.p4_execution_equal(c,condition)
            except Unsupported:continue
            if not self.p1_active_part(template,part):raise Unsupported('Field original conditional control is in an inactive HTML slot')
            target=y if selected=='consequent'else n;other=n if selected=='consequent'else y
            self.p4_execution_equal(other,"''")
            if len(target)!=1 or target[0].kind not in {'template','string'}:raise Unsupported('Field original conditional control is not a literal selected child template')
            matches.append(target)
        if len(matches)!=1:raise Unsupported('Field original conditional control branch differs')
        return matches[0]

    def p4_field_exact_event(self,tokens,event,callee,constructor,expected):
        # Same handler may provide edit and readonly preview. Select the
        # exact original argument tuple; legacy unique-event grammar is not
        # weakened or caught-and-overridden.
        html,expressions=self.p4_execution_concat(tokens);parser=TableHTMLContext(expressions);parser.feed(html);parser.close();hits=[]
        for actual,handler in parser.handlers:
            if actual!=event:continue
            value=handler.strip().removesuffix(';').strip()
            if value in expressions:
                cs=calls(expressions[value])
                if len(cs)!=1 or cs[0].path!=(constructor['name'],)or cs[0].index!=0 or cs[0].end_index!=len(expressions[value])-1 or not cs[0].args or len(cs[0].args[0])!=1:continue
                method=literal(cs[0].args[0][0])
                if constructor['owner']+(method,)!=normalized_callee(callee):continue
                args=cs[0].args[1:]
            else:
                cs=calls(lex(value))
                if len(cs)!=1 or cs[0].path!=normalized_callee(callee):continue
                args=[]if not any(cs[0].args)else cs[0].args
                args=[expressions.get(literal(arg[0]),arg)if len(arg)==1 and arg[0].kind=='string'else arg for arg in args]
            if len(args)!=len(expected):continue
            if all(self.p4_execution_tokens(arg)==self.p4_execution_tokens(lex(wanted))for arg,wanted in zip(args,expected)):hits.append(args)
        if len(hits)!=1:raise Unsupported('Field control lacks one exact active original owner/identity/mode argument tuple')
        return hits[0]

    def p4_field_render_default(self,render):
        if [self.p4_execution_tokens(p)for p in render['params']]!=[self.p4_execution_tokens(lex('kind=activeKind'))]:raise Unsupported('Field original page does not preserve its selected tab as its default render argument')

    def p4_field_kind_entry(self,flow,steps):
        op=flow['entry_binding'];render=steps['render'];links=steps['links'];select=steps['select'];columns=steps['columns'];actions=steps['row_actions'];f=render['function'];m=render['model'];parent=render['parent']
        route=self.p4_flow_route(op.get('route'));pub=self.p4_flow_export(op.get('render_publication'))
        if pub['function']is not f or any(s['model']is not m or s['parent']is not parent for s in steps.values()):raise Unsupported('Field module named functions are not the actual published lexical owner')
        if route['route_page']!='business-categories'or route['route_view']is not None or op.get('selected_kind')!='field':raise Unsupported('Field source must start at its real category page and explicit original field tab')
        self.p4_flow_ui_alias(op.get('ui_alias_evidence'),'ui',parent,f.body_start+1)
        self.p2_const_alias(self.evidence(op.get('model_alias_evidence')),'model','CustomerBusiness',parent,f.body_start+1)
        wrapper=self.p4_field_root_pattern(m,parent,op.get('page_wrapper_evidence'),"window.Pages['business-categories']={render:()=>render(),refresh};")
        # The dynamic compatibility registration excludes the actual category
        # wrapper, rather than allowing arbitrary computed Pages mutation.
        self.p4_field_root_pattern(m,parent,op.get('route_names_evidence'),"const routeNames={category:'business-categories',field:'business-fields'};")
        compatibility=self.p4_field_root_pattern(m,parent,op.get('page_wrapper_evidence'),"Object.entries(routeNames).filter(([kind])=>kind!=='category').forEach(([kind,route])=>{window.Pages[route]={render:()=>render(kind),refresh};});")
        allowed_windows={id(wrapper[0])}|{id(t)for t in compatibility if t.value=='window'}
        initializer=self.p4_field_root_pattern(m,parent,op.get('page_initializer_evidence'),'window.Pages=window.Pages||{};')
        allowed_windows|={id(t)for t in initializer if t.value=='window'}
        for ts in m.lists:
            for i,t in enumerate(ts):
                if t.kind!='id'or t.value!='window'or i and ts[i-1].value in {'.','?.'}:continue
                path=path_at(ts,i,False,partial=True)
                if not path:continue
                actual,end=path
                if actual[:2]!=('window','Pages'):continue
                if id(t)in allowed_windows:continue
                if actual==('window','Pages')or actual[:3]==('window','Pages','business-categories'):
                    self.p4_no_write_contexts(m,{'window'},t.start-1,t.end+1)
                    if end>=len(ts)or ts[end].value not in {'.','['}:raise Unsupported('Original field wrapper/page map escapes through an unproven value')
        self.p4_field_root_pattern(m,parent,op.get('kind_state_evidence'),"const kindNames={category:'业务分类',field:'自定义字段'};")
        self.p4_field_root_pattern(m,parent,op.get('kind_state_evidence'),"const states={category:{keyword:'',status:''},field:{keyword:'',status:''}};")
        self.p4_execution_equal(select['body'],'if(!states[kind])return;activeKind=kind;if(rerender)refresh();')
        state_decl=self.p4_field_root_pattern(m,parent,op.get('active_kind_evidence'),"let tenantId='',context='',editor=null,activeKind='category',flowChanged=false;")
        state_token=next(t for t in state_decl if t.value=='activeKind')
        select_write=self.p4_execution_pattern(select,'activeKind=kind;',root=True)[0]
        render_write=self.p4_execution_pattern(render,"activeKind=states[kind]?kind:'category';kind=activeKind;",root=True)[0]
        self.p4_no_write_contexts(m,{'activeKind'},0,len(m.source)+1,[state_token,select_write,render_write])
        self.p4_execution_pattern(render,"activeKind=states[kind]?kind:'category';kind=activeKind;",root=True)
        self.p4_field_render_default(render)
        self.p4_execution_pattern(render,"if(!model.canManage())return ui.empty('当前账号无业务表单配置权限');",root=True)
        self.p4_execution_pattern(render,'const tenants=syncScope(),data=catalog(),rows=data.ok?sortedRows(kind,data):[];',root=True)
        ret=self.p4_execution_return(render)
        if len(ret)!=1 or ret[0].kind!='template':raise Unsupported('Field source page is not one actual returned template')
        for name in ['links','filterHtml']:
            cs=[c for c in m.calls if c.path==(steps[name if name=='links'else'filter']['function'].symbol,)and f.body_start<c.start<c.end<f.body_end]
            if len(cs)!=1 or not any(k=='expression'and part and part[0].start==cs[0].start and part[-1].end==cs[0].end and self.p1_active_part(ret[0],part)for k,part in ret[0].parts):raise Unsupported('Field selection/filter helper is not actually returned in active HTML')
        # Object.keys of the original literal kindNames reaches the real field
        # selector button; no role/tab string alone counts as a source entry.
        lr=self.p4_execution_return(links)
        if len(lr)!=1 or lr[0].kind!='template':raise Unsupported('Original field selector is not returned literal HTML')
        choices=[]
        for k,part in lr[0].parts:
            if k!='expression'or not self.p1_active_part(lr[0],part):continue
            if len(part)>10 and [t.value for t in part[:10]]==['Object','.','keys','(','kindNames',')','.','map','(','key']:
                close=matching(part)[8];cb=part[9:close]
                if len(cb)==3 and [t.value for t in cb[:2]]==['key','=>']and cb[2].kind=='template':choices.append(cb[2]);self.p4_execution_equal(part[close+1:],".join('')")
        if len(choices)!=1:raise Unsupported('Original field selector has no exact literal-kind map/button')
        args=self.p4_execution_html_event([choices[0]],'click','BusinessConfiguration.selectKind')
        if len(args)!=1:raise Unsupported('Original field selector argument count differs')
        self.p4_execution_equal(args[0],'key')
        for call in m.calls:
            if call.path in {('Object','assign'),('Object','defineProperty'),('Object','defineProperties'),('Object','setPrototypeOf'),('Reflect','set'),('Reflect','deleteProperty')}and call.args:
                p=path_at(call.args[0],0,False)
                if p and p[0][:2]==('window','Pages'):raise Unsupported('Original field page wrapper is modified by an unproven mutator')
        selector_pub=self.p4_flow_export(op.get('select_publication'))
        if selector_pub['function']is not select['function']:raise Unsupported('Original field selector points at another implementation')
        self.p4_execution_equal(steps['rows']['body'],"return kind==='category'?data.rows:data.fields;")
        self.p4_execution_pattern(steps['sorted'],'const rows=rowsOf(kind,data).filter(row=>(!state.keyword||row.label.toLocaleLowerCase().includes(state.keyword.toLocaleLowerCase()))&&(!state.status||row.enabled===(state.status===\'enabled\')));',root=True)
        self.p4_execution_equal(self.p4_execution_return(steps['sorted']),'ui.sortByUpdated?.(rows,[])||rows')
        # Literal builder appends the actual id callback to the same table.
        pushes=[c for c in m.calls if c.path==('common','push')and m.enclosing_function(c.start)is columns['function']]
        final=[]
        for call in pushes:
            for arg in call.args:
                p=self.p1_props(arg)
                if self.p1_literal(p.get('key',[]))=='id':self.p4_execution_equal(p.get('render',[]),'(_,row)=>'+actions['function'].symbol+'(kind,row,data)');final.append(call)
        if len(pushes)!=3 or len(final)!=1:raise Unsupported('Field table id renderer does not come from its original finite columns builder')
        self.p4_execution_equal(self.p4_execution_return(columns),'common')
        for call in m.calls:
            if columns['function'].body_start<call.start<columns['function'].body_end and call.path[:1]==('common',)and call not in pushes:raise Unsupported('Field columns builder modifies the selected original column through an unknown operation')
        table=[c for c in m.calls if c.path==('ui','table')and m.enclosing_function(c.start)is f]
        if len(table)!=1:raise Unsupported('Field source list has no unique actual original table')
        self.p4_execution_equal(table[0].args[0],columns['function'].symbol+'(kind,data)');self.p4_execution_equal(table[0].args[1],'rows')
        self.p1_table_contract(self.evidence(flow['event_binding'].get('table_implementation_evidence')),self.evidence(flow['event_binding'].get('table_export_evidence')),'id')
        if not any(k=='expression'and part and part[0].start==table[0].start and part[-1].end==table[0].end and self.p1_active_part(ret[0],part)for k,part in ret[0].parts):raise Unsupported('Field original table is not in active returned page content')
        return dict(route=route,render=render,template=ret[0],table=table[0],row_actions=actions,state_binding_token=state_token,selector_function=select['function'])

    def p4_field_kind_flow(self,flow):
        op=flow['entry_binding'];operation=op.get('operation');event=flow['event_binding'];steps=self.p4_execution_steps(flow);base=self.p4_field_kind_entry(flow,steps);render=steps['render'];actions=steps['row_actions'];opening=steps['open'];draw=steps['draw'];save=steps['save'];terminal=self.p4_execution_named(flow.get('terminal_binding',flow['result_binding']));component=self.p4_execution_named(flow['result_binding'].get('component'))
        if operation not in {'query','detail','create','edit','enable','disable','delete'}:raise Unsupported('Original field operation is not one selected source operation')
        self.p4_execution_equal(steps['active']['body'],"return !!editor&&model.canManage()&&editor.context===model.context()&&model.tenants().some(row=>row.tenantId===editor.scope.tenantId);")
        if [self.p4_execution_tokens(p)for p in opening['params']]!=[self.p4_execution_tokens(lex(x))for x in ['kind','id','previewOnly=false']]:raise Unsupported('Field original opener defaults/argument slots differ')
        ctor=self.p4_execution_constructor(event.get('constructor'),render)
        if operation=='query':
            args=self.p4_execution_html_event(self.p4_execution_return(steps['filter']),'click','BusinessConfiguration.query',ctor)
            if len(args)!=1:raise Unsupported('Original field query loses its selected kind')
            self.p4_execution_equal(args[0],'kind')
            self.p4_execution_pattern(steps['query'],"if(!states[kind])return;",root=True)
            self.p4_execution_pattern(steps['query'],"states[kind].keyword=document.getElementById('business-config-keyword')?.value.trim()||'';",root=True)
            self.p4_execution_pattern(steps['query'],"states[kind].status=document.getElementById('business-config-status')?.value||'';",root=True)
            self.p4_execution_pattern(steps['query'],'refresh();',root=True)
            entry=steps['query'];result=render
        else:
            ar=self.p4_execution_return(actions)
            if len(ar)!=1 or ar[0].kind!='template':raise Unsupported('Original field row controls are not literal returned HTML')
            selected=ar
            if operation in {'enable','disable'}:selected=self.p4_field_selected_child(ar[0],'protectedField','alternate')
            if operation=='delete':selected=self.p4_field_selected_child(ar[0],"kind==='category'||protectedField||referenced",'alternate')
            callee='BusinessConfiguration.'+('open'if operation in {'detail','edit'}else'toggle'if operation in {'enable','disable'}else'remove')
            if operation=='create':
                toolbar=[c for c in render['model'].calls if c.path==('ui','toolbar')and render['model'].enclosing_function(c.start)is render['function']]
                if len(toolbar)!=1:raise Unsupported('Original field create has no unique active toolbar')
                self.p4_flow_ui_slot(event.get('toolbar_slot'));selected=toolbar[0].args[0];callee='BusinessConfiguration.open'
            expected=['kind']if operation=='create'else['kind','row.id','true']if operation=='detail'else['kind','row.id','!row.enabled']if operation in {'enable','disable'}else['kind','row.id']
            args=self.p4_field_exact_event(selected,'click',callee,ctor,expected)
            if len(args)!=len(expected):raise Unsupported('Original field operation argument slots differ')
            for arg,wanted in zip(args,expected):self.p4_execution_equal(arg,wanted)
            if operation in {'detail','create','edit'}:
                self.p4_execution_pattern(opening,"if(!states[kind]||!model.canManage()||context!==model.context())return showToast('工作范围已变化，请刷新后操作','warning');",root=True)
                self.p4_execution_pattern(opening,'const value=id?rowsOf(kind,data).find(row=>row.id===id):initial(kind);',root=True)
                capture=self.p4_execution_pattern(opening,'editor={kind,values:clone(value),initialValues:clone(value),scope:clone(currentScope()),context:model.context(),revision:data.revision,previewOnly,creating:!id};',root=True)
                self.p4_execution_pattern(opening,'draw();',root=True)
                self.p4_no_write_contexts(opening['model'],{'editor'},opening['function'].body_start,opening['function'].body_end,[capture[0]])
                self.p2_state_mutators(opening['model'],{'editor'},capture[-1].end,opening['function'].body_end,label='Field original editor identity')
                self.p2_protected_escapes(opening['model'],{'editor'},capture[-1].end,opening['function'].body_end,label='Field original editor identity')
                self.p4_no_write_contexts(draw['model'],{'editor'},draw['function'].body_start,draw['function'].body_end)
                self.p2_state_mutators(draw['model'],{'editor'},draw['function'].body_start,draw['function'].body_end,label='Field preview/save identity')
                self.p4_execution_pattern(draw,"if(!isActive()){dismissAll();return showToast('工作范围已变化，请重新打开','warning');}",root=True)
                layer=self.p4_execution_call(draw,'ui.openLayer',event.get('layer_evidence'));self.p4_flow_ui_slot(event.get('open_layer_slot'))
                if len(layer.args)<2 or len(layer.args[1])!=1 or layer.args[1][0].kind!='template':raise Unsupported('Original field editor/preview has no real active layer consumer')
                child=self.p4_field_selected_child(layer.args[1][0],'editor.previewOnly','alternate',event.get('draw_save_expression_index'))
                # This original branch is a quoted HTML string, not a template.
                entry=opening;result=draw
                if operation=='detail':
                    if terminal['function']is not opening['function']:raise Unsupported('Original field preview target must remain open')
                else:
                    if self.p4_execution_html_event(child,'click','BusinessConfiguration.save'):raise Unsupported('Field save has unexpected arguments')
                    self.p4_execution_pattern(save,"if(!isActive()||editor.previewOnly)return error('工作范围已变化，请重新打开');",root=True)
                    self.p4_execution_pattern(save,'const methods={category:model.save,field:model.saveField};',root=True)
                    self.p4_execution_pattern(save,'const result=methods[editor.kind](editor.values,editor.scope,{expectedContext:editor.context,expectedRevision:editor.revision});',root=True)
                    self.p4_execution_pattern(save,'if(!result.ok)return error(result.message);',root=True)
                    methods_token,_=self.p4_execution_const(save,'methods');result_token,result_rhs=self.p4_execution_const(save,'result')
                    self.p4_no_write_contexts(save['model'],{'methods','result'},save['function'].body_start,save['function'].body_end,[methods_token,result_token])
                    self.p2_state_mutators(save['model'],{'methods','result'},save['function'].body_start,save['function'].body_end,label='Field selected method map/result')
                    self.p2_protected_escapes(save['model'],{'methods','result'},save['function'].body_start,save['function'].body_end,[methods_token,result_token,result_rhs[0]],label='Field selected method map/result')
                    self.p4_no_write_contexts(save['model'],{'editor'},save['function'].body_start,result_rhs[-1].end)
                    self.p2_state_mutators(save['model'],{'editor'},save['function'].body_start,result_rhs[-1].end,label='Field captured save identity')
                    self.p2_protected_escapes(save['model'],{'editor'},save['function'].body_start,result_rhs[-1].end,label='Field captured save identity')
                    entry=save;result=save
            elif operation in {'enable','disable'}:
                target=steps['toggle'];self.p4_execution_pattern(target,"if(!states[kind]||!model.canManage()||context!==model.context())return showToast('工作范围已变化，请刷新后操作','warning');",root=True)
                self.p4_execution_pattern(target,'const row=rowsOf(kind,data).find(item=>item.id===id);',root=True)
                self.p4_execution_pattern(target,'const options={expectedContext:data.context,expectedRevision:data.revision};',root=True)
                self.p4_execution_pattern(target,"const result=kind==='category'?model.setEnabled(id,enabled,currentScope(),options):model.saveField({...row,enabled},currentScope(),options);",root=True)
                self.p4_execution_pattern(target,"if(!result.ok)return showToast(result.message,'warning');",root=True)
                if op.get('selected_enabled')is not(operation=='enable'):raise Unsupported('Original field boolean direction differs from its selected operation')
                entry=target;result=target
            else:
                target=steps['remove'];self.p4_execution_pattern(target,"if(kind==='category')return showToast('业务分类暂不支持删除，可停用','warning');",root=True)
                self.p4_execution_pattern(target,'const row=rowsOf(kind,data).find(item=>item.id===id);',root=True)
                confirm=self.p4_execution_call(target,'ui.confirm',target['evidence'].record)
                props=self.p1_props(confirm.args[0]);callback=self.p4_execution_callback_step(target,props.get('onConfirm',[]))
                self.p4_execution_pattern(callback,'const result=model.deleteField(id,currentScope(),{expectedContext:data.context,expectedRevision:data.revision});',root=True)
                self.p4_execution_pattern(callback,'if(!result.ok)return error(result.message);',root=True)
                self.p4_execution_confirm_callback(event.get('confirmation'),target,{'kind':'const_alias','owner':('window','PlatformUI'),'symbol':'ui','evidence':op.get('ui_alias_evidence')})
                entry=target;result=target
        # The selected component is an actual published original local method.
        publication=self.p4_flow_export(flow['result_binding'].get('component_publication'))
        if publication['function']is not component['function']:raise Unsupported('Field source result component publication differs')
        mutation=self.p4_execution_named(flow['result_binding'].get('mutation'))
        if component['function'].symbol in {'saveField','deleteField'}:
            call=self.p4_execution_call(component,mutation['function'].symbol,component['evidence'].record)
            if len(call.args)!=3:raise Unsupported('Field local component mutation arguments differ')
            self.p4_execution_equal(call.args[0],'source');self.p4_execution_equal(call.args[1],'options')
            self.p4_execution_pattern(mutation,"if(!canManage()||!allowed(source))return fail('当前账号无权维护此租户的业务配置');",root=True)
            self.p4_execution_pattern(mutation,"if(options.expectedContext!==undefined&&options.expectedContext!==context())return fail('工作范围已变化，请重新打开后操作');",root=True)
            self.p4_execution_pattern(mutation,'next.revision++;root.localStorage.setItem(storageKey,JSON.stringify(next));')
        selected_control='query'if operation=='query'else'open'if operation=='detail'else'save'if operation in {'create','edit'}else'toggle'if operation in {'enable','disable'}else'remove'
        control_pub=self.p4_flow_export(event.get('control_publication'))
        if control_pub['function']is not entry['function']or self.proof['source_chain']['entry'].get('callee')!='BusinessConfiguration.'+selected_control or self.proof['source_chain']['entry'].get('event')!='click':raise Unsupported('Field source-chain entry differs from its real selected published control')
        if operation in {'create','edit','enable','disable','delete'}:result=component
        decoded={'entry_evidence':entry['evidence'],'terminal_evidence':terminal['evidence'],'terminal_symbol':terminal['function'].symbol,'result_evidence':result['evidence'],'result_symbol':result['function'].symbol,'followup_verified':False}
        if operation=='query':decoded['verified_default_endpoint']={'function':render['function'],'model':render['model'],'evidence':render['evidence'],'selector_function':base['selector_function'],'state_binding_token':base['state_binding_token']}
        return decoded

    def p4i_function(self,spec):
        return self.p4r_function(spec)

    def p4i_service(self,spec,parent):
        if not isinstance(spec,dict)or spec.get('version')!='bounded-extended-mock-service@1':raise Unsupported('Inbound service needs its explicit original factory/const/extension/publication chain')
        fe=self.evidence(spec.get('factory_evidence'));factory=spec.get('factory_symbol');owner=normalized_callee(spec.get('owner'));obj=spec.get('object_symbol')
        if not owner or owner[0]!='window'or len(owner)!=2 or not all(IDENT.fullmatch(x or'')for x in [factory,obj]):raise Unsupported('Inbound original service factory/owner is not a fixed source binding')
        rhs=self.p4_rep_const_arrow(fe,factory,parent)
        self.no_shadow(fe.model,'window',None,[rhs[3].start],include_parent=True)
        if[x.value for x in rhs]!=['(',')','=>',*sum(([p,'.']for p in owner[:-1]),[]),owner[-1]]:raise Unsupported('Inbound service factory returns another source receiver')
        oe=self.evidence(spec.get('object_evidence'));xe=self.evidence(spec.get('extension_evidence'));pe=self.evidence(spec.get('publication_evidence'))
        if not(oe.model is xe.model is pe.model):raise Unsupported('Inbound service object, named extension and publication moved to different sources')
        root=spec.get('root_binding');alias,actual=self.p4_flow_root_alias(root,pe.model,pe.model.scope(next(t.start for ts in pe.model.lists for t in ts if pe.start<=t.start<pe.end and t.value==root.get('alias'))));scope=pe.model.scope(next(t.start for ts in pe.model.lists for t in ts if pe.start<=t.start<pe.end and t.value==alias));model=pe.model
        consts=[]
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts[:-4]):
                if[x.value for x in ts[i:i+4]]==['const',obj,'=','{']and model.scope(t.start)is scope and oe.start<=t.start and ts[ms[i+3]].end<=oe.end:consts.append(ts[i+1])
        if len(consts)!=1:raise Unsupported('Inbound service lacks its original unique const API object')
        if not self.p4_rep_direct_body_token(model,scope,next(t for ts in model.lists for i,t in enumerate(ts[:-1])if t.value=='const'and ts[i+1]is consts[0])):raise Unsupported('Inbound API object is not a direct source binding')
        assignments=[];extensions=[];allowed=consts[:]
        for ts in model.lists:
            for i,t in enumerate(ts[:-6]):
                if[x.value for x in ts[i:i+6]]==[alias,'.',owner[-1],'=',obj,';']and model.scope(t.start)is scope and pe.start<=t.start and ts[i+5].end<=pe.end:assignments.append(t);allowed.append(ts[i+4])
        if len(assignments)!=1:raise Unsupported('Inbound service is not published from the actual const API object')
        for c in model.calls:
            if c.path!=('Object','assign')or len(c.args)!=2 or[x.value for x in c.args[0]]!=[obj]or not xe.contains(c):continue
            fields=c.args[1]
            if not fields or fields[0].value!='{'or matching(fields).get(0)!=len(fields)-1:raise Unsupported('Inbound service extension must be one literal named-member object')
            names={}
            for part in split_tokens(fields[1:-1]):
                if len(part)!=1 or part[0].kind!='id'or part[0].value in names:raise Unsupported('Inbound service extension contains executable/spread/duplicate members')
                name=part[0].value;fn=model.declaration(name,scope)
                if fn is None:raise Unsupported('Inbound service named member is not its exact same-scope implementation')
                names[name]=fn
            if model.enclosing_function(c.start)is not scope:raise Unsupported('Inbound service extension is deferred behind another function')
            extensions.append((c,names));allowed.append(c.args[0][0])
        if len(extensions)!=1:raise Unsupported('Inbound service requires one exact original named Object.assign extension')
        call,names=extensions[0]
        for token in [call.tokens[call.index],assignments[0]]:
            if not self.p4_rep_direct_body_token(model,scope,token):raise Unsupported('Inbound service extension/publication is deferred or hidden')
        needed=spec.get('members',{})
        if not isinstance(needed,dict)or not needed:raise Unsupported('Inbound service needs its exact used member implementations')
        bound={}
        for key,source in needed.items():
            info=self.p4i_function(source)
            if info['symbol']!=key or info['model']is not model or info['function']is not names.get(key):raise Unsupported('Inbound service used member points at another source implementation')
            bound[key]=info
        for ts in model.lists:
            for i,t in enumerate(ts):
                if t.value!=obj or i and ts[i-1].value in {'.','?.'}or any(t is a for a in allowed):continue
                p=path_at(ts,i,False,partial=True)
                if p and(len(p[0])>1 and p[0][1]in names or p[1]<len(ts)and ts[p[1]].value=='['):self.p4_no_write_contexts(model,{obj},t.start-1,t.end+1)
                if p and p[0]==(obj,)and not(p[1]<len(ts)and ts[p[1]].value in {'.','['}):raise Unsupported('Inbound service const API object has an unproved alias/forward escape')
        self.p2_no_owner_writes(model,[(obj,n)for n in names],())
        for c in model.calls:
            if c.path in {('Object','assign'),('Object','defineProperty'),('Object','defineProperties'),('Object','setPrototypeOf'),('Reflect','set'),('Reflect','deleteProperty')}and c.args and[x.value for x in c.args[0]]==[obj]and c is not call:raise Unsupported('Inbound service members are overwritten by another mutation helper')
        self.no_shadow(model,'Object',None,[call.start],include_parent=True)
        for mutation in model.calls:
            if mutation.path in {('Object','assign'),('Object','defineProperty'),('Object','defineProperties'),('Object','setPrototypeOf'),('Reflect','set'),('Reflect','deleteProperty')}and mutation.args:
                target=self.p4_flow_path(mutation.args[0],0,{}, {alias:('window',)})
                if target and target[0][:len(owner)]==owner:raise Unsupported('Inbound published service is mutated through an unproved helper')
        for ts in model.lists:
            for i,t in enumerate(ts):
                if t.kind!='id'or i and ts[i-1].value in {'.','?.'}:continue
                p=self.p4_flow_path(ts,i,{}, {alias:('window',)})
                if not p or p[0][:len(owner)]!=owner:continue
                if t is assignments[0]:continue
                if p[0]==owner and not(p[1]<len(ts)and ts[p[1]].value in {'.','['}):raise Unsupported('Inbound published service owner has an unproved bare copy/forward escape')
                self.p4_no_write_contexts(model,{t.value},t.start-1,t.end+1)
        self.p4i_factory_no_escapes(fe,factory,owner,parent,rhs)
        return dict(factory=factory,owner=owner,model=model,parent=scope,members=bound)

    def p4i_scalar(self,ts,bindings):
        ts=self.p3_unwrap(ts);v=[t.value for t in ts];q=None;colon=None;depth=0;ms=matching(ts);i=0
        while i<len(ts):
            if ts[i].value in {'(','[','{'}:i=ms[i]+1;continue
            if ts[i].value=='?':
                if q is None:q=i
                depth+=1
            elif ts[i].value==':'and q is not None:
                depth-=1
                if not depth:colon=i;break
            i+=1
        if q is not None and colon is not None:
            truth=self.p4i_condition(ts[:q],bindings)
            if type(truth)is not bool:raise Unsupported('Inbound fixed event argument is not selected by its actual source mode')
            return self.p4i_scalar(ts[q+1:colon]if truth else ts[colon+1:],bindings)
        if len(ts)==4 and v[:2]==['Number','(']and v[-1]==')':
            value=self.p4i_scalar(ts[2:-1],bindings)
            if type(value)not in {int,float}and not(isinstance(value,str)and re.fullmatch(r'[12]',value)):raise Unsupported('Inbound numeric state normalization needs its original finite active value')
            return int(value)
        if len(ts)==6 and v[:2]==['Number','(']and v[3]=='.'and v[-1]==')':return self.p4i_scalar(ts[2:-1],bindings)
        if len(ts)==1 and ts[0].value in bindings:return bindings[ts[0].value]
        if len(ts)==3 and v[1]=='.'and (v[0]+'.'+v[2])in bindings:return bindings[v[0]+'.'+v[2]]
        return self.p4_rep_literal(ts)

    def p4i_condition(self,ts,bindings):
        ts=self.p3_unwrap(ts);ms=matching(ts);v=[t.value for t in ts]
        for op in ['||','&&']:
            parts=self.p4_rep_split(ts,op)
            if len(parts)>1:
                vals=[self.p4i_condition(p,bindings)for p in parts]
                if op=='&&':return False if False in vals else True if all(x is True for x in vals)else None
                return True if True in vals else False if all(x is False for x in vals)else None
        if ts and ts[0].value=='!':
            truth=self.p4i_condition(ts[1:],bindings);return not truth if type(truth)is bool else None
        if v==[bindings.get('_factory'),'(',')','.','allowed','(','true',')']:return bindings.get('_manage')
        for i,t in enumerate(ts):
            if t.value in {'===','!=='}:
                try:a=self.p4i_scalar(ts[:i],bindings);b=self.p4i_scalar(ts[i+1:],bindings)
                except Unsupported:return None
                same=type(a)is type(b)and a==b;return same if t.value=='==='else not same
        try:value=self.p4i_scalar(ts,bindings)
        except Unsupported:return None
        return value if type(value)is bool else bool(value)if isinstance(value,str)else None

    def p4i_ancestor(self,root,leaf,bindings):
        if root is leaf:return
        if root.kind!='template':raise Unsupported('Inbound event ancestry is not its actual original template')
        selected=[p for k,p in root.parts if k=='expression'and p and p[0].start<=leaf.start and leaf.end<=p[-1].end]
        if len(selected)!=1 or not self.p1_active_part(root,selected[0]):raise Unsupported('Inbound event is unused or in an inert/attribute ancestor')
        expr=self.p3_unwrap(selected[0])
        while not(len(expr)==1 and expr[0]is leaf):
            if len(expr)==1 and expr[0].kind=='template':return self.p4i_ancestor(expr[0],leaf,bindings)
            ms=matching(expr);q=None;colon=None;depth=0;i=0
            while i<len(expr):
                if expr[i].value in {'(','[','{'}:i=ms[i]+1;continue
                if expr[i].value=='?':
                    if q is None:q=i
                    depth+=1
                elif expr[i].value==':'and q is not None:
                    depth-=1
                    if not depth:colon=i;break
                i+=1
            if q is None or colon is None:raise Unsupported('Inbound event leaves the finite source-conditional template path')
            operands=[expr[q+1:colon],expr[colon+1:]];choices=[i for i,p in enumerate(operands)if p and p[0].start<=leaf.start and leaf.end<=p[-1].end]
            if len(choices)!=1:raise Unsupported('Inbound event branch is missing or ambiguous')
            truth=self.p4i_condition(expr[:q],bindings)
            if type(truth)is not bool or truth!=(choices[0]==0):raise Unsupported('Inbound event is excluded by its original row/mode/permission guard')
            expr=self.p3_unwrap(operands[choices[0]])

    def p4i_event(self,root,event,parent,bindings):
        hits=[]
        for ts in iter_lists([root]):
            for leaf in ts:
                if leaf.kind!='template':continue
                try:
                    e,args=self.p4r_html_event_arguments([leaf],event,parent);self.p4i_ancestor(root,leaf,bindings)
                except Unsupported:continue
                hits.append((leaf,e,args))
        if len(hits)!=1:raise Unsupported('Inbound control has no unique actual active exact source event')
        return hits[0]

    def p4i_initial(self,spec,route,owner):
        lr=self.p4_flow_route(spec['route']);lp=self.p4_flow_export(spec['page_export']);render=self.p4i_function(spec['render']);producer=self.p4i_function(spec['producer'])
        if render['function']is not lp['function']or render['model']is not producer['model']or render['parent']is not producer['parent']:raise Unsupported('Inbound initial frame is not its actual published source page')
        view=lr['route_view'];state=spec['state_variable'];option=spec['option_parameter']
        if view!='inbound'or render['params']!=[option]:raise Unsupported('Inbound initial route does not select its original inbound-service view')
        self.p4_execution_pattern(render,state+'='+option+'&&'+option+'.view||'+state+';',root=True)
        expr=self.p4_rep_return(render,{state:view});cs=calls(expr)
        if len(cs)!=1 or cs[0].index!=0 or cs[0].end_index!=len(expr)-1 or cs[0].path!=(producer['symbol'],)or any(cs[0].args):raise Unsupported('Inbound initial route does not return its actual inbound delegate')
        alias=spec['ui_alias'];self.p4_flow_ui_alias(spec['ui_alias_evidence'],alias,producer['parent'],producer['function'].body_start+1);slot=self.p4_flow_ui_slot(spec['ui_slot'])
        calls_=[c for c in self.p4_rep_calls(producer,alias+'.pageHeader')if len(c.args)>2]
        if len(calls_)!=1 or slot['index']!=2:raise Unsupported('Inbound initial frame lacks its actual header action slot')
        c=calls_[0];self.p4_rep_active_call(producer,c);event=spec['opening_event'];found=[];e=self.evidence(event['evidence'])
        self.p4_execution_pattern(producer,'var canView=window.AliCtiInbound?.allowed(),canManage=window.AliCtiInbound?.allowed(true);',root=True)
        for ts in iter_lists(c.args[2]):
            for leaf in ts:
                if leaf.kind not in {'string','template'}or not e.contains(leaf):continue
                try:
                    self.p4_rep_quoted_control([leaf],event);html,values=self.p4_rep_markup(c.args[2],{'canView':True},needle=(leaf.start,leaf.end));p=TableHTMLContext(values);p.feed(html);p.close()
                    if any(k in p.active and x and(x[0].start,x[-1].end)==(leaf.start,leaf.end)for k,x in values.items()):found.append(leaf)
                except Unsupported:continue
        if len(found)!=1:raise Unsupported('Inbound internal list route has no actual active original opening control')
        op=self.p4i_function(spec['opening']);pub=self.p4_flow_export(spec['opening_publication'])
        if op['function']is not pub['function']or op['model']is not owner['model']or op['parent']is not owner['parent']:raise Unsupported('Inbound initial control publishes another actual opener')
        self.p4_execution_equal(op['body'],"if(service()?.allowed())RouteRuntime.openSecondary("+repr(route['route_key'])+",{refreshOnClose:true});")
        return producer

    def p4i_row_projection(self,spec,info,renderer):
        e=self.evidence(spec['evidence']);name=spec['symbol'];row=spec['parameter'];field=spec['field'];fallback=spec['fallback'];rhs=self.p4_rep_const_arrow(e,name,info['parent'])
        if [t.value for t in rhs[:6]]!=[row,'=>',row,'.',field,'||']or len(rhs)!=7 or self.p4_rep_literal(rhs[6:])!=fallback:raise Unsupported('Inbound row display projection is not an immutable field/fallback reader')
        allowed=[];r=renderer['params'][1]
        for c in info['model'].calls:
            if c.path==(name,)and renderer['template'].start<c.start<renderer['template'].end and len(c.args)==1 and[t.value for t in c.args[0]]==[r]:allowed.append(c.args[0][0]);self.no_shadow(info['model'],name,info['parent'],[c.start])
        if len(allowed)!=1:raise Unsupported('Inbound display reader does not consume the actual selected row once')
        return allowed

    def p4i_context(self,render,table):
        token,rhs=self.p4r_const(render,'key',table['call'].start)
        if[t.value for t in rhs]!=['service','(',')','.','context','(',')']:raise Unsupported('Inbound event context is not the exact current service scope')
        self.p4_no_write_contexts(render['model'],{'key'},token.start-1,render['function'].body_end,[token])
        return token

    def p4i_component_result(self,service,method,spec):
        fn=service['members'].get(method)
        if fn is None:raise Unsupported('Inbound source result calls an unbound service method')
        if method in {'list','get'}:return fn
        write=self.p4i_function(spec['write']);persist=self.p4i_function(spec['persist'])
        if write['model']is not fn['model']or persist['model']is not fn['model']or write['parent']is not fn['parent']or persist['parent']is not fn['parent']:raise Unsupported('Inbound mock write/persist result is disconnected from its actual method')
        expr=self.p4r_return(fn);cs=[c for c in calls(expr)if c.index==0 and c.end_index==len(expr)-1]
        if len(cs)!=1 or cs[0].index!=0 or cs[0].end_index!=len(expr)-1 or cs[0].path!=(write['symbol'],)or len(cs[0].args)!=4:raise Unsupported('Inbound original method does not return its actual four-argument local write')
        if method=='remove'and self.p4_rep_literal(cs[0].args[0])!='delete'or method=='setActive'and self.p4_rep_literal(cs[0].args[0])!='update':raise Unsupported('Inbound original state/delete method writes another operation')
        if method=='save'and[t.value for t in cs[0].args[0]]!=['previous','?',"'update'",':',"'create'"]:raise Unsupported('Inbound original save does not distinguish prior-row update from create')
        expected={'save':"write(previous?'update':'create',f,row,options.context)",'setActive':"write('update',{id:Number(id),active:Number(active)},{...clone(row),active:str(active)},key)",'remove':"write('delete',{id:Number(id)},null,key)"}[method]
        self.p4_execution_equal(expr,expected)
        self.no_shadow(fn['model'],write['symbol'],fn['parent'],[cs[0].start]);pc=self.p4_rep_calls(write,persist['symbol'])
        if len(pc)<1 or any(len(c.args)!=1 or[t.value for t in c.args[0]]!=['next']for c in pc):raise Unsupported('Inbound local write does not persist its actual next snapshot')
        self.p4_execution_pattern(persist,'localStorage.setItem(storageKey,JSON.stringify(next));store=next;return true;')
        self.p4_execution_pattern(write,"if(!persist(next))return failure('保存失败，请检查浏览器存储后重试。');",root=True)
        if not any(t.value=='mock'for t in service['model'].tokens):raise Unsupported('Inbound result source no longer identifies its local mock boundary')
        return write

    def p4i_confirm(self,spec):
        info=self.p4i_function(spec['implementation']);export=self.evidence(spec['export']);bound=self.owner_export(export,'window.PlatformUI.confirm','confirm')
        if bound[0]is not info['function']or info['params']!=['options']:raise Unsupported('Inbound confirm callback is not the actual published UI helper')
        self.p4_execution_pattern(info,"const opts=options||{};",root=True);self.p4_execution_pattern(info,"const id=opts.id||'platform-confirm';",root=True)
        self.p4_execution_pattern(info,"const button=document.getElementById(`${id}-confirm`);",root=True)
        self.p4_execution_pattern(info,"if(button)button.onclick=()=>{if(typeof opts.onConfirm==='function'&&opts.onConfirm()===false)return;closeLayer(id);};",root=True)
        self.p4_flow_ui_slot(spec['layer_contract']);cs=self.p4_rep_calls(info,'openLayer')
        if len(cs)!=1 or len(cs[0].args)<2 or[t.value for t in cs[0].args[0]]!=['id']or len(cs[0].args[1])!=1 or cs[0].args[1][0].kind!='template':raise Unsupported('Inbound real confirmation does not create its active layer')
        root=cs[0].args[1][0];html,parts=template_markup(root);ids=[k for k,v in parts.items()if[t.value for t in v]==['id']]
        class ConfirmButtons(TableHTMLContext):
            def __init__(self,markers):super().__init__(markers);self.ids=[]
            def handle_starttag(self,tag,attrs):
                super().handle_starttag(tag,attrs)
                if tag=='button'and self.stack and not self.stack[-1][1]:self.ids.append(dict(attrs).get('id'))
        parser=ConfirmButtons(parts);parser.feed(html);parser.close()
        if len([v for v in parser.ids if any(v==k+'-confirm'for k in ids)])!=1:raise Unsupported('Inbound confirmation callback has no unique actual active button ID')
        return info

    def p4i_callback_result(self,handler,service,method,confirm_spec):
        self.p4i_confirm(confirm_spec);cs=self.p4_rep_calls(handler,'ui.confirm')
        if len(cs)!=1 or len(cs[0].args)!=1 or not self.p2_direct_statement(handler['body'],cs[0].start):raise Unsupported('Inbound mutation is not attached to its actual original confirmation control')
        props=self.p1_props(cs[0].args[0]);cb=props.get('onConfirm',[]);v=[t.value for t in cb]
        if len(cb)<6 or v[:5]!=['async','(',')','=>','{']or matching(cb).get(4)!=len(cb)-1:raise Unsupported('Inbound confirmed mutation is not the original async callback')
        calls_=[c for c in handler['model'].calls if cb[0].start<c.start<cb[-1].end and c.path==(service['factory']+'()',method)]
        if len(calls_)!=1 or[[t.value for t in a]for a in calls_[0].args]!=[[p]for p in handler['params']]:raise Unsupported('Inbound confirmed mutation changes the original id/state/context argument slots')
        self.p4_no_write_contexts(handler['model'],set(handler['params']),handler['function'].body_start,handler['function'].body_end)
        self.p4_execution_pattern(handler,"if(!service()?.allowed(true)||key!==service().context())return;",root=True)
        return calls_[0]

    def p4i_form(self,flow,render,service,method,owner):
        entry=flow['entry_binding'];event=flow['event_binding'];result=flow['result_binding'];op=self.p4i_function(entry['opening']);mode=flow['profile_kind'];opening_event=entry['opening_event'];bindings={'_factory':service['factory'],'_manage':True,'readonly':False}
        if op['function']is not self.p4_flow_export(entry['opening_publication'])['function']or op['model']is not render['model']or op['parent']is not render['parent']:raise Unsupported('Inbound form opening is not the same original published source owner')
        if mode=='create':
            tc=self.p4_rep_calls(render,'ui.toolbar')
            if len(tc)!=1 or[t.value for t in tc[0].args[0]]!=['actions']:raise Unsupported('Inbound create does not consume its actual toolbar actions binding')
            self.p4_rep_active_call(render,tc[0]);token,rhs=self.p4r_const(render,'actions',tc[0].start)
            if not self.p4_rep_direct_body_token(render['model'],render['function'],next(t for ts in render['model'].lists for t in ts if t.start==token.start-6 and t.value=='const')):raise Unsupported('Inbound create action binding is deferred')
            # This is the actual original manage qualification, not a new role.
            ms=matching(rhs);q=next((i for i,t in enumerate(rhs)if t.value=='?'),None)
            if q is None or[t.value for t in rhs[:q]]!=['service','(',')','.','allowed','(','true',')']or len(rhs[q+1:])!=3 or rhs[q+1].kind!='template'or rhs[q+2].value!=':'or self.p4_rep_literal(rhs[q+3:])!='':raise Unsupported('Inbound create action is not its original permission-controlled template')
            _,_,args=self.p4i_event(rhs[q+1],opening_event,render['parent'],bindings)
            if len(args)!=2 or self.p4i_scalar(args[0],bindings)!=''or[t.value for t in args[1]]!=['key']:raise Unsupported('Inbound create opener does not use its original empty-id/current-context slots')
        else:
            table=self.p4r_table(render,entry['content'],'ui');self.p4i_context(render,table);bindings.update({table['params'][1]+'.pending':False})
            _,_,args=self.p4i_event(table['template'],opening_event,render['parent'],bindings)
            if len(args)!=3 or self.p4_identity_origin(args[0],table['params'],'id')!='value'or[t.value for t in args[1]]!=['key']or self.p4i_condition(args[2],bindings)is not False:raise Unsupported('Inbound edit opener does not preserve the exact editable id/context/readOnly slots')
        layer=self.evidence(result['layer_evidence']);roots=[c.args[1][0]for c in self.p4_rep_calls(op,'ui.openLayer')if len(c.args)>1 and len(c.args[1])==1 and c.args[1][0].kind=='template'and layer.contains(c.args[1][0])]
        if len(roots)!=1:raise Unsupported('Inbound original form lacks one exact main layer template')
        lc=[c for c in self.p4_rep_calls(op,'ui.openLayer')if len(c.args)>1 and c.args[1]==[roots[0]]]
        if len(lc)!=1 or self.p4_rep_literal(lc[0].args[0])!='route-detail'or not self.p2_direct_statement(op['body'],lc[0].start):raise Unsupported('Inbound save control is not inserted by its actual original main layer call')
        self.p4_flow_ui_slot(result['layer_contract']);self.p4i_event(roots[0],event,op['parent'],bindings)
        self.p4_execution_pattern(op,'const resources=service().resources(),readonly=!!id&&(readOnly||!service().allowed(true));',root=True)
        self.p4_execution_pattern(op,'const current={id:String(id),key:service().context(),expectedRevision:result.revision,readonly,busy:false};dialog=current;',root=True)
        self.p4_execution_pattern(op,"if(!service()?.allowed()||(key&&key!==service().context())||(!id&&!service().allowed(true)))return;",root=True)
        save=self.p4i_function(result['terminal'])
        if save['function']is not owner['function']or save['params']:raise Unsupported('Inbound save event targets another original implementation')
        self.p4_execution_pattern(save,'const current=dialog;',root=True)
        self.p4_execution_pattern(save,'if(!current||current.busy||current.readonly||current.key!==service().context()||!service().allowed(true))return;',root=True)
        self.p4_execution_pattern(save,'const result=await service().save(input,{id:current.id,context:current.key,expectedRevision:current.expectedRevision});')
        self.p4_execution_pattern(save,'if(dialog!==current||current.key!==service().context())return;')
        self.p4_execution_pattern(save,'if(!result.ok){feedback(result);return;}')
        self.p4_execution_pattern(save,"if(!current.id)input.name=val('routeName');",root=True)
        self.p4_execution_pattern(save,'close();refreshViews();')
        self.p2_state_mutators(save['model'],{'current','result'},save['function'].body_start,save['function'].body_end,label='Inbound captured save identity/result')
        # Busy is the original UI submit lock. It cannot be used as an alias
        # exception for id, context, readonly or local expected revision.
        allowed=[]
        for ts in save['model'].lists:
            for i,t in enumerate(ts[:-3]):
                if t.value=='current'and[t.value for t in ts[i+1:i+4]]==['.','busy','=']:allowed.append(t)
                if i and ts[i-1].value=='const'and t.value in {'current','result'}:allowed.append(t)
        self.p4_no_write_contexts(save['model'],{'current','result'},save['function'].body_start,save['function'].body_end,allowed)
        identity_allowed=[t for t in allowed if t.value=='current']
        for ts in save['model'].lists:
            for i,t in enumerate(ts):
                if t.value=='current'and i>=2 and ts[i-2].value=='dialog'and ts[i-1].value in {'===','!=='}:identity_allowed.append(t)
        self.p4r_captured_escapes(save,'current',identity_allowed)
        feedback=self.p4i_function(result['feedback'])
        if feedback['model']is not save['model']or feedback['parent']is not save['parent']or feedback['params']!=['result']:raise Unsupported('Inbound feedback is not the original same-owner result reader')
        self.p4_no_write_contexts(feedback['model'],{'result'},feedback['function'].body_start,feedback['function'].body_end);self.p2_state_mutators(feedback['model'],{'result'},feedback['function'].body_start,feedback['function'].body_end);self.p2_protected_escapes(feedback['model'],{'result'},feedback['function'].body_start,feedback['function'].body_end,label='Inbound feedback result')
        result_allowed=[t for t in allowed if t.value=='result']
        for call in self.p4_rep_calls(save,feedback['symbol']):
            if len(call.args)==1 and[t.value for t in call.args[0]]==['result']:result_allowed.append(call.args[0][0])
        self.p4r_captured_escapes(save,'result',result_allowed)
        return save

    def p4_inbound_flow(self,flow):
        if flow.get('version')!='bounded-active-source-flow@1'or flow.get('reachability')!='conditional_only'or flow.get('family')!='resource':raise Unsupported('Inbound source flow needs its explicit bounded resource envelope')
        entry=flow.get('entry_binding',{});event=flow.get('event_binding',{});result=flow.get('result_binding',{});kind=flow.get('profile_kind')
        if entry.get('kind')!='inbound_rule'or kind not in {'query','detail','create','edit','enable','disable','delete'}:raise Unsupported('Unsupported finite original inbound-rule source flow')
        route=self.p4_flow_route(entry['route']);page=self.p4_flow_export(entry['page_export']);render=self.p4i_function(entry['render']);owner=self.p4_flow_export(event['publication']);terminal=self.p4i_function(result['terminal']);actual=self.proof['source_chain']['entry']
        if render['function']is not page['function']or render['model']is not owner['model']or render['parent']is not owner['parent']or normalized_callee(event['callee'])!=normalized_callee(actual.get('callee'))or event['event']!=actual.get('event'):raise Unsupported('Inbound route/render/actual event owner is disconnected')
        ee=self.evidence(event['evidence']);self.p4_flow_ui_alias(entry['ui_alias_evidence'],'ui',render['parent'],render['function'].body_start+1);service=self.p4i_service(entry['service_binding'],render['parent']);self.p4i_initial(entry['initial_frame'],route,page)
        self.p4_execution_pattern(render,"if(!service()?.allowed())return ui.empty('当前工作范围无呼入规则查看权限');",root=True)
        if kind=='query':
            root=self.p4r_return(render)
            if len(root)!=1 or root[0].kind!='template':raise Unsupported('Inbound query is not in its actual returned rule-list template')
            self.p4i_event(root[0],event,render['parent'],{});query=self.p4i_function(result['query'])
            if query['function']is not owner['function']or terminal['function']is not render['function']:raise Unsupported('Inbound query does not return its original result renderer')
            self.p4_execution_equal(query['body'],"keyword=document.getElementById('routeKeyword')?.value||'';status=document.getElementById('routeStatus')?.value||'';RouteRuntime.refreshCurrent();")
            outcome=self.p4i_component_result(service,'list',result)
        elif kind in {'create','edit'}:
            selected=self.p4i_form(flow,render,service,'save',owner)
            if terminal['function']is not selected['function']:raise Unsupported('Inbound form changes the original save terminal')
            outcome=self.p4i_component_result(service,'save',result)
        else:
            table=self.p4r_table(render,entry['content'],'ui');self.p4i_context(render,table);r=table['params'][1];bindings={'_factory':service['factory'],'_manage':kind!='detail',r+'.pending':False,r+'.active':1 if kind=='disable'else 2}
            leaf,_,args=self.p4i_event(table['template'],event,render['parent'],bindings)
            if not args or self.p4_identity_origin(args[0],table['params'],'id')not in {'row','value'}:raise Unsupported('Inbound row event uses a display name or another identity field')
            allowed=[]
            if kind=='detail':
                if self.p4_identity_origin(args[0],table['params'],'id')!='row'or len(args)!=3 or self.p4i_scalar(args[2],bindings)is not True:raise Unsupported('Inbound detail must use the original name-column id and explicit readOnly=true control')
                allowed=self.p4i_row_projection(entry['row_display_projection'],render,table)
            self.p2_protected_escapes(render['model'],{r},table['template'].start,table['template'].end,allowed,label='Inbound actual row identity')
            context_index=2 if kind in {'enable','disable'}else 1
            if len(args)<=context_index or[t.value for t in args[context_index]]!=['key']:raise Unsupported('Inbound event drops its exact current source context')
            if kind in {'enable','disable'}and(self.p4i_scalar(args[1],bindings)!=(1 if kind=='enable'else 2)):raise Unsupported('Inbound state action is not selected from its actual original numeric active branch')
            if terminal['function']is not owner['function']:raise Unsupported('Inbound table event changes the original complete handler')
            if kind=='detail':outcome=self.p4i_component_result(service,'get',result)
            else:
                method='remove'if kind=='delete'else'setActive';self.p4i_callback_result(terminal,service,method,result['confirm_contract']);outcome=self.p4i_component_result(service,method,result)
        needed={'initial_frame_to_rule_list','actual_rule_control','original_identity_context','original_local_mock_result'}
        if {s.get('kind')for s in flow['flow_steps']}!=needed:raise Unsupported('Inbound actual source-edge declarations are incomplete')
        return dict(entry_evidence=ee,terminal_evidence=terminal['evidence'],terminal_symbol=terminal['symbol'],result_evidence=outcome['evidence'],result_symbol=outcome['symbol'])

    def p4i_factory_no_escapes(self,e,factory,owner,parent,rhs):
        declarations=[t for ts in e.model.lists for i,t in enumerate(ts[:-1])if t.value==factory and i and ts[i-1].value=='const'and e.model.scope(t.start)is parent and e.start<=t.start<e.end]
        if len(declarations)!=1:raise Unsupported('Inbound service factory lacks its exact immutable declaration')
        for ts in e.model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts):
                if t.kind!='id'or i and ts[i-1].value in {'.','?.'}:continue
                p=path_at(ts,i,False,partial=True)
                if p and p[0][:len(owner)]==owner:
                    if any(t is r for r in rhs):continue
                    if p[0]==owner and not(p[1]<len(ts)and ts[p[1]].value in {'.','['}):raise Unsupported('Inbound factory owner is copied outside its exact fixed return')
                    self.p4_no_write_contexts(e.model,{t.value},t.start-1,t.end+1)
                if t.value!=factory or any(t is d for d in declarations):continue
                if i+2>=len(ts)or[t.value for t in ts[i+1:i+3]]!=['(',')']:raise Unsupported('Inbound immutable service factory has an unproved function copy/forward use')
                j=i+3
                if j>=len(ts)or ts[j].value not in {'.','?.','['}:raise Unsupported('Inbound service factory result has an unproved whole-object copy/forward escape')
                while j<len(ts)and ts[j].value in {'.','?.','['}:j=ms[j]+1 if ts[j].value=='['else j+2
                ops='';at=j
                while at<len(ts)and ts[at].kind=='punct'and ts[at].value not in {'(',')','[',']','{','}',',',';',':','?'}:ops+=ts[at].value;at+=1
                if '='in ops and ops not in {'===','!==','==','!=','<=','>='}or ops in {'++','--'}or i and ts[i-1].value in {'delete','++','--'}:raise Unsupported('Inbound factory service member is rewritten')

    # Explicit original queue picker/configuration flow. This source parser
    # does not execute JS or infer supplier/API success from local persistence.
    def p4_queue_markup(self,tokens,selectors):
        if not isinstance(selectors,dict)or any(not isinstance(k,str)or type(v)is not bool for k,v in selectors.items()):raise Unsupported('Queue HTML selectors must be internally proven finite booleans')
        values={};serial=[0]
        def opaque(part):
            key='__QUEUE_SOURCE_'+str(serial[0])+'__';serial[0]+=1;values[key]=part;return key
        def visit(part):
            part=self.p3_unwrap(part)
            if not part:raise Unsupported('Queue original HTML expression is empty')
            try:c,y,n=self.p4_execution_ternary(part)
            except Unsupported:c=None
            if c is not None:
                hits=[v for k,v in selectors.items()if self.p4_execution_tokens(c)==self.p4_execution_tokens(lex(k))]
                if len(hits)==1:return visit(y if hits[0]else n)
                return opaque(part)
            if len(part)==1 and part[0].kind=='string':return self.p4_quoted_html(part[0])
            if len(part)==1 and part[0].kind=='template':
                return ''.join(text if kind=='text'else visit(text)for kind,text in part[0].parts)
            # Only literal plus-concatenation is projected. Unknown values are
            # opaque source markers, never evaluated or searched recursively.
            ms=matching(part);groups=[];at=0;i=0
            while i<len(part):
                if part[i].value in {'(','[','{'}:i=ms[i]+1;continue
                if part[i].value=='+':groups.append(part[at:i]);at=i+1
                i+=1
            groups.append(part[at:])
            if len(groups)>1:return ''.join(visit(group)for group in groups)
            return opaque(part)
        return visit(tokens),values

    def p4_queue_projected_event(self,tokens,selectors,callee):
        html,values=self.p4_queue_markup(tokens,selectors);parser=TableHTMLContext(values);parser.feed(html);parser.close();hits=[]
        for event,handler in parser.handlers:
            if event!='click':continue
            cs=calls(lex(handler.strip()))
            if len(cs)==1 and cs[0].path==normalized_callee(callee)and not any(cs[0].args):hits.append(cs[0])
        if len(hits)!=1:raise Unsupported('Queue original selected active footer/control is missing or ambiguous')
        return hits[0]

    def p4_queue_frame(self,flow,steps):
        op=flow['entry_binding'];frame=op.get('resource_frame',{});route=self.p4_flow_route(frame.get('route'));page=self.p4_flow_export(frame.get('page_export'));render=steps['render']
        if page['function']is not render['function']:raise Unsupported('Queue frame is not its actual original published render')
        self.p4_flow_ui_alias(frame.get('ui_alias_evidence'),'ui',render['parent'],render['function'].body_start+1)
        self.p4_execution_pattern(render,"if(!allowed())return ui.empty('当前账号无队列管理权限');",root=True)
        api_binding=self.p4_field_root_pattern(render['model'],render['parent'],op.get('page_factory_evidence'),'const api=()=>AliCtiQueues,context=()=>api().contextKey();')
        aliases=[api_binding[1]]+[t for i,t in enumerate(api_binding[:-1])if t.value=='context'and api_binding[i+1].value=='=']
        self.p2_no_owner_writes(render['model'],[('api',),('context',)],aliases)
        self.no_shadow(render['model'],'AliCtiQueues',None,[api_binding[0].start],include_parent=True)
        self.p2_no_owner_writes(render['model'],[('AliCtiQueues',),('window','AliCtiQueues')])
        kind=op.get('operation');method='openCreate'if kind=='create'else'openDetails'if kind=='edit'else'openAssociation'
        event=flow['event_binding'];helper_spec=dict(event.get('handler_helper',{}));helper_spec['helper_call']=dict(name=helper_spec.get('helper_call',{}).get('name'),literal_method=method)
        helper=self.p4r_escaped_helper(helper_spec,render['parent'],render['function'].body_start+1)
        expected_callee='QueueManagement.'+method
        if helper['owner']+(helper['method'],)!=normalized_callee(expected_callee):raise Unsupported('Queue original entry helper receiver/method differs')
        if kind=='create':
            template=self.p4_execution_return(render)[0]
            cs=[c for c in render['model'].calls if c.path==('ui','toolbar')and render['model'].enclosing_function(c.start)is render['function']]
            if len(cs)!=1 or not any(k=='expression'and p[0].start==cs[0].start and p[-1].end==cs[0].end and self.p1_active_part(template,p)for k,p in template.parts):raise Unsupported('Queue create toolbar is not actual returned active HTML')
            self.p4_flow_ui_slot(event.get('toolbar_slot'));selected=cs[0].args[0];expected=['key']
        else:
            ri=self.p4r_function(frame.get('render'));renderer=self.p4r_table(ri,frame.get('content'),'ui')
            if renderer['params']!=['v','q']or frame['content'].get('column_key_literal')!='qno':raise Unsupported('Queue action column no longer preserves its original qno/row slots')
            selected=self.p4_field_selected_child(renderer['template'],"q.status==='ACTIVE'",'consequent')
            expected=['v','true','key']if kind=='edit'else['v','key']
        self.p4_field_exact_event(selected,'click',expected_callee,{'name':helper['name'],'owner':helper['owner']},expected)
        opening=steps['open_create'if kind=='create'else'open_details'if kind=='edit'else'open_assoc'];pub=self.p4_flow_export(event.get('opening_publication'))
        if pub['function']is not opening['function']:raise Unsupported('Queue original opening control resolves to another published implementation')
        return dict(render=render,opening=opening,route=route)

    def p4_queue_picker(self,flow,steps,mode,button):
        opening=steps['open_create'if mode=='create'else'open_assoc'];begin=steps['begin'];draw=steps['draw_picker'];selected=steps.get('selected');event=flow['event_binding']
        if mode=='create':
            self.p4_execution_pattern(opening,'if(!allowed(key))return denied();',root=True)
            self.p4_execution_pattern(opening,"const scope=tenants();if(scope.length!==1)return showToast('请先为当前账号绑定唯一的有效租户','warning');beginPicker({kind:'create',context:context(),tenantId:scope[0].tenantId,selectedIds:[],initialIds:[]});",root=True)
        else:
            self.p4_execution_equal(opening['body'],"if(!allowed(key))return denied();const info=api().describeQueue(qno);if(!info||info.queue.status!=='ACTIVE')return denied();const ids=groupIds(info);beginPicker({kind:'association',qno,context:context(),tenantId:info.queue.tenantId,revision:info.revision,selectedIds:ids.slice(),initialIds:ids.slice()});")
        capture=self.p4_execution_pattern(begin,'picker={...value,dirty:false,routeHash:location.hash};drawPicker();return true;',root=True)
        self.p4_execution_pattern(draw,'if(!picker||!allowed(picker.context))return denied();',root=True)
        self.p4_execution_pattern(draw,"const create=picker.kind==='create',members=picker.kind==='members',info=create?null:api().describeQueue(picker.qno),tenant=tenants().find(t=>t.tenantId===picker.tenantId);",root=True)
        self.p4_execution_pattern(draw,'if(!tenant||!create&&!info)return denied();',root=True)
        layer=self.p4_execution_call(draw,'ui.openLayer',draw['evidence'].record);self.p4_flow_ui_slot(event.get('open_layer_slot'))
        if len(layer.args)<2:raise Unsupported('Queue picker lacks its real original layer consumer')
        self.p4_execution_active_value(layer.args[1],'footer')
        _,footer=self.p4_execution_const(draw,'footer')
        selectors={'members':False,'create':mode=='create'}
        if button=='verifyCurrent':selectors['!create&&linkedGroups(info).length']=True
        self.p4_queue_projected_event(footer,selectors,'QueueManagement.'+button)
        if selected is not None:
            self.p4_execution_equal(selected['body'],'const available=availableGroups();return picker.selectedIds.every(id=>available.some(g=>g.physicalGroupId===id))?picker.selectedIds.slice():null;')
        return capture

    def p4_queue_component_result(self,result,spec):
        pub=self.p4_flow_export(spec.get('publication'));mutation=self.p4_execution_named(spec.get('mutation'))
        if pub['function']is not result['function']or mutation['model']is not result['model']or mutation['parent']is not result['parent']:raise Unsupported('Queue local result/transaction/publication do not belong to the same original component')
        self.p4_execution_pattern(mutation,"if(contextKey()!==expectedContext)return failure('登录或租户范围已变化，请重新打开"+('队列配置'if mutation['function'].symbol=='transactQueue'else'接听配置')+"');",root=True)
        if mutation['function'].symbol=='transactQueue':
            self.p4_execution_pattern(mutation,"if(!canAccess()||typeof qno!=='string'||!qno)return failure('仅超级管理员或本租户管理员可维护当前范围的队列');",root=True)
            self.p4_execution_pattern(mutation,"if(!Number.isSafeInteger(expectedRevision)||expectedRevision!==latest.value.revision||snapshot.revision!==latest.value.revision)return failure('队列配置已更新，请刷新后重新打开配置');",root=True)
            self.p4_execution_pattern(mutation,"if(queue.status!=='ACTIVE')return failure('该队列已暂停，当前仅可查看');",root=True)
        else:
            self.p4_execution_pattern(mutation,"const group=groupFor(input);if(!canManage(group))return failure('仅超级管理员或本租户管理员可维护当前范围的接听配置');",root=True)
            self.p4_execution_pattern(mutation,"if(!Number.isSafeInteger(expectedRevision)||expectedRevision!==latest.value.revision||snapshot.revision!==latest.value.revision)return failure('队列关联已更新，请刷新后重新打开配置');",root=True)
        actual_return=self.p4_execution_return(result);actual_calls=[c for c in calls(actual_return)if c.path==(mutation['function'].symbol,)and c.index==0 and c.end_index==len(actual_return)-1]
        if len(actual_calls)!=1:raise Unsupported('Queue published method does not return its actual original guarded transaction')
        self.p4_execution_pattern(mutation,'localStorage.setItem(storageKey,JSON.stringify(next));')
        self.p4_execution_pattern(mutation,"snapshot=next;rawSeen=JSON.stringify(next);errorMessage='';",root=True)
        self.p4_execution_pattern(mutation,'if(result?.ok===false)return result;',root=True)
        return pub

    def p4_queue_create_anchor(self,anchor):
        self.p4_execution_equal(anchor['body'],"if(!validPicker()||picker.kind!=='create')return denied();const ids=selectedGroups();if(!ids?.length)return pickerError('请至少选择一个当前租户可关联的技能。');const key=picker.context;closePicker(true);QueueConfig.openCreate(ids,key);")
        return {'selection_ids':self.p4_execution_pattern(anchor,'const ids=selectedGroups();',root=True),'context':self.p4_execution_pattern(anchor,'const key=picker.context;',root=True)}

    def p4_queue_flow(self,flow):
        op=flow['entry_binding'];kind=op.get('operation');steps=self.p4_execution_steps(flow);frame=self.p4_queue_frame(flow,steps);event=flow['event_binding'];rb=flow['result_binding'];component=self.p4_execution_named(rb.get('component'));publication=self.p4_queue_component_result(component,rb);terminal=self.p4_execution_named(flow.get('terminal_binding',rb));followup=False
        if kind in {'create','associate','verify-members'}:
            mode='create'if kind=='create'else'association';button='continueCreate'if kind=='create'else'saveAssociation'if kind=='associate'else'verifyCurrent';picker_capture=self.p4_queue_picker(flow,steps,mode,button)
        if kind=='create':
            anchor=steps['continue'];opening=steps['config_open'];draw=steps['config_draw'];save=steps['config_save']
            captured=self.p4_queue_create_anchor(anchor)
            self.p4_execution_pattern(opening,'const ids=[...new Set(groupIds||[])],groups=ids.map(groupFor);',root=True)
            self.p4_execution_pattern(opening,"if(!ids.length||groups.some(g=>!g)||groups.some(g=>g.tenantId!==groups[0].tenantId||g.enterpriseId!==groups[0].enterpriseId)||ids.some(id=>!service().canManage(id)||service().describe(id)?.binding)||context!==service().contextKey())return showToast('请选择当前租户尚未关联队列的技能','warning');",root=True)
            editor_capture=self.p4_execution_pattern(opening,"editor={id:ids[0],groupIds:ids,editable:true,context,revision:current.revision,current,mode:'new',selected:'',values:defaults(groups[0].name),dirty:false,routeHash:location.hash};",root=True)
            self.p4_execution_pattern(opening,'draw();',root=True)
            self.p4_no_write_contexts(opening['model'],{'editor'},editor_capture[0].start-1,opening['function'].body_end,[editor_capture[0]])
            self.p2_state_mutators(opening['model'],{'editor'},editor_capture[-1].end,opening['function'].body_end,label='Queue second-stage editor')
            self.p2_protected_escapes(opening['model'],{'editor'},editor_capture[-1].end,opening['function'].body_end,label='Queue second-stage editor')
            context_guard=self.p4_execution_named(op.get('config_context'))
            if context_guard['function']is not save['model'].declaration('contextValid',save['parent']):raise Unsupported('Queue second-stage context guard is from another lexical state')
            self.p4_execution_equal(context_guard['body'],"return editor&&editor.context===service()?.contextKey()&&(editor.groupIds||[editor.id]).every(id=>service().canView(id)&&(!editor.editable||service().canManage(id)));")
            self.p4_execution_pattern(draw,"if(!contextValid())return error('工作范围已变化，请关闭后重新打开。');",root=True)
            layer=self.p4_execution_call(draw,'ui.openLayer',draw['evidence'].record);self.p4_flow_ui_slot(event.get('open_layer_slot'));self.p4_execution_active_value(layer.args[1],'footer')
            _,footer=self.p4_execution_const(draw,'footer');self.p4_queue_projected_event(footer,{'state.editable':True},'QueueConfig.save')
            savepub=self.p4_flow_export(event.get('save_publication'));openpub=self.p4_flow_export(event.get('config_open_publication'))
            if savepub['function']is not save['function']or openpub['function']is not opening['function']:raise Unsupported('Queue next-step configuration/save publication differs')
            self.p4_execution_pattern(save,"if(!contextValid()||!editor.editable)return error('工作范围已变化，请关闭后重新打开。');",root=True)
            commit=self.p4_execution_call(save,'service().createForGroups',save['evidence'].record)
            self.p4_execution_equal(commit.args[0],'editor.groupIds');self.p4_execution_equal(commit.args[1],'payload');self.p4_execution_equal(commit.args[2],'editor.context');self.p4_execution_equal(commit.args[3],'editor.revision')
            self.p4_execution_pattern(save,"result=editor.mode==='new'&&editor.groupIds?service().createForGroups(editor.groupIds,payload,editor.context,editor.revision):service()[editor.mode==='new'?'createForGroup':'updateQueue'](editor.id,payload,editor.context,editor.revision);")
            self.p4_execution_pattern(save,'if(!result.ok)return error(result.message);',root=True)
            self.p4_no_write_contexts(save['model'],{'editor'},save['function'].body_start,commit.end)
            self.p2_state_mutators(save['model'],{'editor'},save['function'].body_start,commit.end,label='Queue captured selection/context')
            self.p2_protected_escapes(save['model'],{'editor'},save['function'].body_start,commit.end,label='Queue captured selection/context')
            factory=self.p4_flow_factory(op.get('config_factory'));component.update(call=commit,publication=publication,factory=factory)
            save['event_evidence']=draw['evidence'];save['capture_proofs']={**captured,'mode':editor_capture,'revision':editor_capture}
            followup=self.p4_captured_user_save_contract(flow,anchor,save,component);entry=anchor;result=component
        elif kind=='associate':
            target=steps['save_assoc'];self.p4_execution_pattern(target,"if(!validPicker()||picker.kind!=='association')return denied();const ids=selectedGroups();if(!ids)return pickerError('技能关联已变化，请重新打开。');",root=True)
            confirm_guard=self.p4_execution_pattern(target,'if(!ids.length&&picker.initialIds.length&&!confirmed){',root=True)
            confirm=self.p4_execution_call(target,'ui.confirm',target['evidence'].record);props=self.p1_props(confirm.args[0]);self.p4_execution_equal(props.get('onConfirm',[]),'()=>saveAssociation(true)')
            # The exact original self-confirm branch is consumed by the actual
            # published UI confirm helper; its complete handler rechecks scope.
            confirmed=self.p4_execution_confirm_callback(event.get('confirmation'),target,{'kind':'const_alias','owner':('window','PlatformUI'),'symbol':'ui','evidence':flow['entry_binding']['resource_frame']['ui_alias_evidence'],'guarded_call':{'call':confirm,'tokens':confirm_guard}})
            if confirmed.get('expression_only')is not True:raise Unsupported('Queue confirmation must retain its original expression callback')
            self.p4_execution_equal(confirmed['body'],'saveAssociation(true)')
            commit=self.p4_execution_call(target,'api().saveQueueGroups',target['evidence'].record)
            self.p4_execution_equal(commit.args[0],'picker.qno');self.p4_execution_equal(commit.args[1],'ids');self.p4_execution_equal(commit.args[2],'picker.context');self.p4_execution_equal(commit.args[3],'picker.revision')
            self.p4_execution_pattern(target,'const result=api().saveQueueGroups(picker.qno,ids,picker.context,picker.revision);if(!result.ok)return pickerError(result.message);',root=True)
            self.p4_no_write_contexts(target['model'],{'picker'},target['function'].body_start,commit.end)
            self.p2_state_mutators(target['model'],{'picker'},target['function'].body_start,commit.end,label='Queue association capture')
            self.p2_protected_escapes(target['model'],{'picker'},target['function'].body_start,commit.end,label='Queue association capture')
            entry=target;result=component
        elif kind=='verify-members':
            control=steps['verify_current'];target=steps['verify']
            self.p4_execution_equal(control['body'],"if(!validPicker()||picker.kind!=='association')return denied();if(picker.dirty)return pickerError('请先保存技能选择，再核对成员。');if(verify(picker.qno,picker.context)){picker.revision=api().revision();drawPicker();}")
            self.p4_execution_pattern(target,'if(!allowed(key))return denied();const info=api().describeQueue(qno),first=linkedGroups(info)[0];if(!first)return denied();',root=True)
            self.p4_execution_pattern(target,"const result=api().verify(first.physicalGroupId,context(),info.revision);if(!result.ok)return showToast(result.message,'warning');",root=True)
            entry=control;result=target
        elif kind=='edit':
            opening=steps['detail_open'];draw=steps['detail_draw'];save=steps['detail_save'];forward=steps['open_details']
            self.p4_execution_equal(forward['body'],'if(!allowed(key))return denied();QueueDetail.open(qno,editable,context());')
            self.p4_execution_pattern(opening,"if(typeof qno!=='string'||!qno||!queues?.canAccess()||context!==undefined&&context!==queues.contextKey())return error('当前工作范围无队列查看权限，请重新打开页面。');",root=True)
            self.p4_execution_pattern(opening,"if(editable&&!current.editable)return error(current.blockedReason||'此队列当前不可编辑。');",root=True)
            capture=self.p4_execution_pattern(opening,'editor={qno,editable:!!editable,context:queues.contextKey(),revision:current.revision,current,values:formFor(current.queue),dirty:false,routeHash:location.hash};',root=True)
            self.p4_execution_pattern(opening,'draw();',root=True)
            layer=self.p4_execution_call(draw,'ui.openLayer',draw['evidence'].record);self.p4_flow_ui_slot(event.get('open_layer_slot'));self.p4_queue_projected_event(layer.args[1],{'editor.editable':True},'QueueDetail.save')
            opub=self.p4_flow_export(event.get('detail_open_publication'));spub=self.p4_flow_export(event.get('save_publication'))
            if opub['function']is not opening['function']or spub['function']is not save['function']:raise Unsupported('Queue independent edit/save owner differs')
            self.p4_execution_pattern(save,"if(!sameContext()||!editor.editable)return error('工作范围已变化，请关闭后重新打开。');",root=True)
            self.p4_execution_pattern(save,'const current=scopedValue();',root=True)
            self.p4_execution_pattern(save,"if(!current.editable)return error(current.blockedReason||'此队列当前不可编辑，填写内容已保留。');",root=True)
            self.p4_execution_pattern(save,'const payload=changes();',root=True)
            self.p4_execution_pattern(save,'const result=service().updateByNumber(editor.qno,payload,editor.context,editor.revision);',root=True)
            commit=self.p4_execution_call(save,'service().updateByNumber',save['evidence'].record)
            pure=self.p4_field_root_pattern(save['model'],save['parent'],op.get('detail_string_evidence'),"const string=value=>value==null?'':String(value);")
            self.p2_no_owner_writes(save['model'],[('string',)],[pure[1]])
            self.no_shadow(save['model'],'string',save['parent'],[save['function'].body_start+1])
            self.no_shadow(save['model'],'String',None,[pure[0].start],include_parent=True)
            self.p2_no_owner_writes(save['model'],[('String',),('window','String')])
            # The original pure string(value).trim() guards read editable
            # scalar values, not a parenthesized editor identity receiver.
            scalar_reads=[]
            for call in save['model'].calls:
                if call.path!=('string',)or save['model'].enclosing_function(call.start)is not save['function']or len(call.args)!=1:continue
                if [t.value for t in call.args[0]]in [['editor','.','values','.',key]for key in ['announceSoundFrequency','announceSoundFile','announcePositionParam']]:scalar_reads.append(call.args[0][0])
            self.p4_no_write_contexts(save['model'],{'editor'},save['function'].body_start,commit.end,scalar_reads)
            self.p2_state_mutators(save['model'],{'editor'},save['function'].body_start,commit.end,label='Queue detail id/context')
            self.p2_protected_escapes(save['model'],{'editor'},save['function'].body_start,commit.end,label='Queue detail id/context')
            self.p4_execution_pattern(save,"if(!result?.ok)return error(result?.message||'保存失败，填写内容已保留，请重试。');",root=True)
            entry=save;result=component
        else:raise Unsupported('Queue source operation is outside the finite original flow')
        claimed=self.proof['source_chain']['entry'];expected='QueueManagement.'+('continueCreate'if kind=='create'else'saveAssociation'if kind=='associate'else'verifyCurrent')if kind!='edit'else'QueueDetail.save'
        if claimed.get('callee')!=expected or claimed.get('event')!='click':raise Unsupported('Queue chain entry differs from the actual original selected control')
        return {'entry_evidence':entry['evidence'],'terminal_evidence':terminal['evidence'],'terminal_symbol':terminal['function'].symbol,'result_evidence':result['evidence'],'result_symbol':result['function'].symbol,'followup_verified':followup}

    def p4m_var(self,info,name,root=True):
        found=[];body=info['body']
        for ts in info['model'].lists:
            ms=matching(ts)
            for i,t in enumerate(ts[:-3]):
                if[x.value for x in ts[i:i+3]]!=['var',name,'=']or info['model'].enclosing_function(t.start)is not info['function']:continue
                if root and not self.p4_execution_direct_statement(body,t.start):continue
                j=i+3
                while j<len(ts)and ts[j].value!=';':j=ms[j]+1 if ts[j].value in {'(','[','{'}else j+1
                if j>=len(ts):raise Unsupported('Member entry var initializer is unterminated')
                found.append((ts[i+1],ts[i+3:j]))
        if len(found)!=1:raise Unsupported('Member entry lacks one original direct var binding: '+name)
        return found[0]

    def p4m_call(self,info,path):
        hits=[c for c in info['model'].calls if c.path==path and info['model'].enclosing_function(c.start)is info['function']]
        if len(hits)!=1:raise Unsupported('Member entry lacks its exact unique call: '+'.'.join(path))
        return hits[0]

    def p4r_account_membership_entry(self,spec):
        if not isinstance(spec,dict):raise Unsupported('Member table entry needs an explicit finite source binding')
        info=self.p4_execution_named(spec.get('render'));model=info['model'];fn=info['function'];e=info['evidence'];row_source=self.p4_execution_named(spec.get('rows'))
        if row_source['model']is not model or row_source['parent']is not info['parent']or row_source['function'].symbol!='accountRows'or fn.symbol!='renderAccounts':raise Unsupported('Member entry row source/render moved from their original lexical owner')
        self.p4_flow_ui_alias(spec.get('ui_alias_evidence'),'ui',info['parent'],fn.start)
        ee=self.evidence(spec.get('event_evidence'))
        if ee.model is not model:raise Unsupported('Member entry event moved from its actual source')
        escape=self.evidence(spec.get('escape_alias_evidence'));escape_defs=[]
        for ts in model.lists:
            for i,t in enumerate(ts[:-6]):
                if[x.value for x in ts[i:i+7]]==['var','esc','=','ui','.','escape',';']and model.scope(t.start)is info['parent']and escape.start<=t.start and ts[i+6].end<=escape.end:escape_defs.append(ts[i+1])
        if escape.model is not model or len(escape_defs)!=1:raise Unsupported('Member entry escaping is not its actual same-scope var UI.escape alias')
        self.p2_no_owner_writes(model,[('esc',),('ui','escape')],escape_defs)
        for ts in model.lists:
            for i,t in enumerate(ts):
                if t.value!='esc'or i and ts[i-1].value in {'.','?.'}or t is escape_defs[0]:continue
                if i+1>=len(ts)or ts[i+1].value!='(':raise Unsupported('Member escape alias is copied, forwarded or shadowed')
        self.p2_escape(self.evidence(spec.get('escape_implementation_evidence')),self.evidence(spec.get('escape_export_evidence')))
        self.p4_execution_pattern(info,'var rows=accountRows();',root=True)
        sort=self.p4_execution_pattern(info,'rows=ui.sortByUpdated?.(rows)||rows;',root=True)
        cloud,_=self.p4m_var(info,'cloudDomain');self.p4_execution_equal(self.p4m_var(info,'cloudDomain')[1],'true')
        page,pexpr=self.p4m_var(info,'pageRows')
        self.p4_execution_equal(pexpr,"rows.slice((accountPage-1)*accountPageSize,accountPage*accountPageSize).map(function(row,index){return Object.assign({orderNo:(accountPage-1)*accountPageSize+index+1},row);})")
        self.no_shadow(model,'Object',None,[page.start],include_parent=True)
        self.p4_no_write_contexts(model,{'rows'},fn.body_start,fn.body_end,[self.p4m_var(info,'rows')[0],sort[0]])
        self.p4_no_write_contexts(model,{'pageRows','cloudDomain'},fn.body_start,fn.body_end,[page,cloud])
        # accountRows explicitly projects the original membership identity.
        projections=[]
        for ts in model.lists:
            for i,t in enumerate(ts[:-4]):
                if[x.value for x in ts[i:i+5]]==['membershipId',':','member','.','membershipId']and row_source['function'].body_start<t.start<row_source['function'].body_end:projections.append(t)
        if len(projections)!=1:raise Unsupported('Member row identity is not the original membershipId projection')
        declaration,initial=self.p4m_var(info,'columns')
        if not initial or initial[0].value!='['or matching(initial).get(0)!=len(initial)-1:raise Unsupported('Member table initial columns are not literal')
        for col in split_tokens(initial[1:-1]):
            if col and self.p1_literal(self.p1_props(col).get('key',[]))in {None,'membershipId'}:raise Unsupported('Member initial column has unknown/conflicting identity key')
        pushes=[c for c in model.calls if c.path==('columns','push')and model.enclosing_function(c.start)is fn];splices=[c for c in model.calls if c.path==('columns','splice')and model.enclosing_function(c.start)is fn]
        if len(pushes)!=2 or len(splices)!=1:raise Unsupported('Member table columns have unknown additions/replacements')
        selected=[c for c in pushes if len(c.args)==3]
        if len(selected)!=1 or not self.p4_execution_direct_statement(info['body'],selected[0].start):raise Unsupported('Member action columns are not directly appended to the real array')
        selected=selected[0];col=self.p1_props(selected.args[-1])
        if self.p1_literal(col.get('key',[]))!='membershipId':raise Unsupported('Member selected renderer has the wrong original object-key slot')
        self.p4_execution_equal(selected.args[0],"...(!cloudDomain?[{key:'updatedBy',label:'更新人'}]:[])")
        if self.p1_literal(self.p1_props(selected.args[1]).get('key',[]))!='updatedAt':raise Unsupported('Member intermediate appended column is not the original timestamp metadata')
        self.p4_execution_equal(splices[0].args[0],'2');self.p4_execution_equal(splices[0].args[1],'0');self.p4_execution_equal(splices[0].args[2],'seatColumns[1]')
        other=next(c for c in pushes if c is not selected)
        if len(other.args)!=1:raise Unsupported('Member seat column has unknown forwarded values')
        self.p4_execution_equal(other.args[0],'seatColumns[0]')
        for call in [splices[0],other]:
            guards=self.p4_rep_call_guards(info,call)
            if len(guards)!=1 or[x.value for x in guards[0][0]]!=['cloudDomain']or guards[0][1]is not True:raise Unsupported('Member seat column projection is not in its real cloud branch')
        seat_columns=self.p4m_var(info,'seatColumns',root=False)[1]
        if not seat_columns or seat_columns[0].value!='['or matching(seat_columns).get(0)!=len(seat_columns)-1:raise Unsupported('Member supplementary seat columns are not literal')
        if len(split_tokens(seat_columns[1:-1]))!=2 or any(self.p1_literal(self.p1_props(x).get('key',[]))!='accountId'for x in split_tokens(seat_columns[1:-1])):raise Unsupported('Member seat columns can replace selected identity')
        maps=self.p4m_call(info,('columns','map'))
        if len(maps.args)!=1:raise Unsupported('Member columns layout map callback is ambiguous')
        self.p4_execution_equal(maps.args[0],"function(column,index){return Object.assign({},column,{width:cloudWidths[index]+'px',className:(column.className||'')+' cloud-account-col-'+index});}")
        prefix=maps.tokens[max(0,maps.index-2):maps.index]
        if[x.value for x in prefix]!=['columns','=']:raise Unsupported('Member column map does not replace the same original array')
        self.p4_no_write_contexts(model,{'columns'},fn.body_start,fn.body_end,[declaration,prefix[0]])
        allowed_calls={id(c)for c in pushes+splices+[maps]}
        if any(c.path[:1]==('columns',)and id(c)not in allowed_calls for c in model.calls if model.enclosing_function(c.start)is fn):raise Unsupported('Member columns use an unknown mutating method')
        body,bexpr=self.p4m_var(info,'body');table=self.p4m_call(info,('ui','table'))
        if len(table.args)!=3 or[x.value for x in table.args[0]]!=['columns']or[x.value for x in table.args[1]]!=['pageRows']or not bexpr or bexpr[0].start!=table.start or bexpr[-1].end!=table.end:raise Unsupported('Member body does not consume its exact real columns/rows table')
        self.p4r_captured_escapes(info,'columns',[declaration,prefix[0],table.args[0][0]],pushes+splices+[maps])
        self.p4r_captured_escapes(info,'pageRows',[page,table.args[1][0]])
        slice_call=self.p4m_call(info,('rows','slice'));self.p4r_captured_escapes(info,'rows',[self.p4m_var(info,'rows')[0]]+[t for t in sort if t.value=='rows'],[slice_call])
        self.p1_table_contract(self.evidence(spec.get('table_implementation_evidence')),self.evidence(spec.get('table_export_evidence')),'membershipId')
        self.p4_no_write_contexts(model,{'body'},fn.body_start,fn.body_end,[body])
        markup,expressions=self.p4_execution_concat(self.p4_execution_return(info));parser=TableHTMLContext(expressions);parser.feed(markup);parser.close()
        if not any(k in parser.active and[x.value for x in v]==['body']for k,v in expressions.items()):raise Unsupported('Member actual table body is unused or enters an inert/attribute slot')
        rv=col.get('render',[])
        if len(rv)<6 or[x.value for x in rv[:2]]!=['function','(']:raise Unsupported('Member action renderer is not its original anonymous function')
        fs=[f for f in model.functions if f.start==rv[0].start and f.parent is fn]
        if len(fs)!=1:raise Unsupported('Member action renderer lacks its actual lexical function')
        rf,params,rbody=self.p4_flow_body(dict(function=fs[0],model=model))
        if params!=['value','row']:raise Unsupported('Member renderer object identity parameter slots changed')
        renderer=dict(function=rf,model=model,evidence=e,parent=fn,params=params,body=rbody)
        self.p4_no_write_contexts(model,{'value','row'},rf.body_start,rf.body_end);self.p2_protected_escapes(model,{'row'},rf.body_start,rf.body_end,label='Member renderer actual row')
        sa,initial=self.p4m_var(renderer,'seatAction');self.p4_execution_equal(initial,"''");assignments=[]
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts[:-2]):
                if t.value!='seatAction'or ts[i+1].value!='='or model.enclosing_function(t.start)is not rf or t is sa:continue
                end=i+2
                while end<len(ts)and ts[end].value!=';':end=ms[end]+1 if ts[end].value in {'(','[','{'}else end+1
                assignments.append((t,ts[i+2:end]))
        if len(assignments)!=1:raise Unsupported('Member seat action is absent or rewritten')
        token,rhs=assignments[0];fake=Call((),[token]+rhs,0,0,len(rhs),[])
        guards=self.p4_rep_call_guards(renderer,fake)
        if len(guards)!=1 or[x.value for x in guards[0][0]]!=['cloudDomain']or guards[0][1]is not True:raise Unsupported('Member seat action is disconnected from its proven cloud branch')
        if not(ee.start<=token.start and rhs[-1].end<=ee.end):raise Unsupported('Member actual event quote misses its full source assignment')
        self.p4_no_write_contexts(model,{'seatAction'},rf.body_start,rf.body_end,[sa,token])
        rh,rex=self.p4_execution_concat(self.p4_execution_return(renderer));rp=TableHTMLContext(rex);rp.feed(rh);rp.close()
        if not any(k in rp.active and[x.value for x in v]==['seatAction']for k,v in rex.items()):raise Unsupported('Member seat action is unused or inert in the actual row HTML')
        html,parts=self.p4_execution_concat(rhs);attribute=[]
        for key,value in parts.items():
            if[x.value for x in value[:2]]==['error','?']:
                self.p4_execution_equal(value,"error?' disabled title=\"'+esc(error)+'\"':''");attribute.append(key)
        if len(attribute)!=1:raise Unsupported('Member dynamic button fragment is not its original escaped disabled/title-only attribute')
        html=html.replace(attribute[0],'');hp=TableHTMLContext(parts);hp.feed(html);hp.close();hits=[];callee="window.Pages['account-tenant'].openAccountSeat"
        for kind,handler in hp.handlers:
            if kind!='click':continue
            cc=calls(lex(handler))
            if len(cc)!=1 or cc[0].path!=normalized_callee(callee)or len(cc[0].args)!=1:continue
            arg=cc[0].args[0];marker=self.p1_literal(arg);actual=parts.get(marker)
            if actual is None:continue
            self.p4_execution_equal(actual,'esc(value)');hits.append(actual)
        if len(hits)!=1:raise Unsupported('Member seat action has the wrong actual callee/identity argument')
        return dict(renderer=renderer,entry_evidence=ee,arguments=[hits[0]],identity_parameter='value',identity_field='membershipId',callee=callee)

    def p3_unwrap(self,ts):
        while ts and ts[0].value=='(' and matching(ts).get(0)==len(ts)-1:ts=ts[1:-1]
        return ts

    def p3_literal(self,ts):
        ts=self.p3_unwrap(ts)
        if len(ts)==1 and ts[0].kind=='string':
            value=literal(ts[0])
            if value is None:raise Unsupported('P3 string comparison literal has unsupported escaping')
            return ('string',value)
        if len(ts)==1 and ts[0].value in {'true','false','null'}:
            return ('null',None)if ts[0].value=='null'else('boolean',ts[0].value=='true')
        text=''.join(t.value for t in ts)
        if (len(ts)==1 and ts[0].kind=='number'or len(ts)==2 and ts[0].value=='-'and ts[1].kind=='number')and re.fullmatch(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?',text):
            value=float(text)
            if value==value and value not in {float('inf'),float('-inf')}:return ('number',value)
        raise Unsupported('P3 condition needs one supported primitive literal')

    def p3_claim_literal(self,value):
        if value is None:return ('null',None)
        if type(value)is bool:return ('boolean',value)
        if isinstance(value,str):return ('string',value)
        if type(value)in {int,float}:
            try:number=float(value)
            except (ValueError,OverflowError):raise Unsupported('P3 numeric claim is not finite')
            if number==number and number not in {float('inf'),float('-inf')}:return ('number',number)
        raise Unsupported('P3 metadata literal is not a supported primitive')

    def p3_ternary(self,ts,row,field):
        ts=self.p3_unwrap(ts);ms=matching(ts);question=None;at=0
        while at<len(ts):
            if ts[at].value in {'(','[','{'}:at=ms[at]+1;continue
            if ts[at].value=='?':question=at;break
            at+=1
        if question is None:raise Unsupported('P3 path is not an exact row-field literal ternary')
        condition=self.p3_unwrap(ts[:question])
        if len(condition)<5 or [t.value for t in condition[:3]]!=[row,'.',field]or condition[3].value not in {'===','!=='}:raise Unsupported('P3 comparison uses a different row/field or unsupported operator/condition')
        value=self.p3_literal(condition[4:]);depth=0;colon=None;at=question+1
        while at<len(ts):
            if ts[at].value in {'(','[','{'}:at=ms[at]+1;continue
            if ts[at].value=='?':depth+=1
            elif ts[at].value==':':
                if depth==0:colon=at;break
                depth-=1
            at+=1
        if colon is None or colon==question+1 or colon==len(ts)-1:raise Unsupported('P3 ternary has an incomplete selected branch')
        return condition[3].value,value,{'consequent':ts[question+1:colon],'alternate':ts[colon+1:]}

    def p3_operand(self,ts,row,field,depth=0):
        if depth>32:raise Unsupported('P3 ternary nesting exceeds its finite bound')
        ts=self.p3_unwrap(ts)
        if len(ts)==1 and ts[0].kind=='template':return
        if len(ts)==1 and ts[0].kind=='string'and literal(ts[0])=='':return
        _,_,parts=self.p3_ternary(ts,row,field)
        for part in parts.values():self.p3_operand(part,row,field,depth+1)

    def p3_literal_context(self,template,row,field,seen=None):
        seen=set()if seen is None else seen
        if template.start in seen:return
        seen.add(template.start)
        if len(seen)>128:raise Unsupported('P3 literal template tree exceeds its finite bound')
        html,_=template_markup(template);parser=P3BalancedHTML();parser.feed(html);parser.close()
        if not parser.valid or parser.stack:raise Unsupported('P3 literal template has an unbalanced ancestor/sibling HTML context')
        for kind,part in template.parts:
            if kind!='expression':continue
            # Active text peers can emit markup. Only the same finite literal
            # ternary/empty forms may establish an unaffected active context.
            if self.p1_active_part(template,part):self.p3_operand(part,row,field)
            for ts in iter_lists(part):
                for token in ts:
                    if token.kind=='template':self.p3_literal_context(token,row,field,seen)

    def p3_renderer_readonly(self,model,root,params):
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts[:-1]):
                if t.value not in params or not(root.start<t.start<root.end)or i and ts[i-1].value in {'.','?.'}:continue
                p=path_at(ts,i,False,partial=True)
                if not p:continue
                _,end=p
                while end<len(ts)and ts[end].value in {'.','['}:end=ms[end]+1 if ts[end].value=='['else end+2
                if end<len(ts)and ts[end].value in {'=','+=','-=','++','--'}or i and ts[i-1].value=='delete':raise Unsupported('P3 renderer row/value parameter or field is rewritten')
        self.p2_state_mutators(model,set(params),root.start,root.end,label='P3 renderer row/value')
        # Only the row object is protected against unproved copy/forward
        # escape; the value parameter retains original esc/command uses.
        self.p2_protected_escapes(model,{params[1]},root.start,root.end,label='P3 renderer row')

    def p3_nested_event_template(self,spec,e,root,params):
        if not isinstance(spec,dict)or spec.get('version')!='bounded-row-ternary-template@1'or spec.get('reachability')!='conditional_only':raise Unsupported('Unsupported explicit P3 nested conditional event profile')
        row=spec.get('row_parameter');field=spec.get('row_field');path=spec.get('path')
        if len(params)<2 or row!=params[1]or not IDENT.fullmatch(field or ''):raise Unsupported('P3 condition row/field is not the original renderer row parameter')
        if not isinstance(path,list)or not 2<=len(path)<=32:raise Unsupported('P3 needs a complete finite interpolation/branch path')
        self.p3_renderer_readonly(e.model,root,params);self.p3_literal_context(root,row,field)
        node=root;equals=None;not_equals=set();conditions=0;renderer=e.model.enclosing_function(root.start)
        for step in path:
            if not isinstance(step,dict):raise Unsupported('P3 path step must be structured')
            if isinstance(node,Token)and node.kind=='template':
                if step.get('kind')!='interpolation'or type(step.get('expression_index'))is not int:raise Unsupported('P3 template path must select its actual interpolation')
                parts=[part for kind,part in node.parts if kind=='expression'];index=step['expression_index']
                if not 0<=index<len(parts)or not self.p1_active_part(node,parts[index]):raise Unsupported('P3 ancestor interpolation is not active HTML text')
                node=parts[index];continue
            if not isinstance(node,list)or step.get('kind')!='ternary':raise Unsupported('P3 path skips or misorders an actual conditional/template node')
            operator,value,parts=self.p3_ternary(node,row,field);branch=step.get('branch')
            if 'literal'not in step or branch not in {'consequent','alternate'}or step.get('operator')!=operator or self.p3_claim_literal(step['literal'])!=value:raise Unsupported('P3 selected branch metadata differs from the exact original comparison')
            self.p3_operand(node,row,field);positive=(operator=='===')==(branch=='consequent')
            if positive:
                if equals is not None and equals!=value or value in not_equals:raise Unsupported('P3 selected branch contradicts an ancestor row constraint')
                equals=value
            else:
                if equals==value:raise Unsupported('P3 selected branch is dead under its ancestor equality')
                not_equals.add(value)
            conditions+=1;selected=self.p3_unwrap(parts[branch])
            node=selected[0]if len(selected)==1 and selected[0].kind=='template'else selected
            self.no_shadow(e.model,row,renderer,[selected[0].start])
        if not conditions or not isinstance(node,Token)or node.kind!='template'or node is root or not e.contains(node):raise Unsupported('P3 path does not terminate at the exact actual selected child template')
        return node

    def supported_template_context(self,e,template,ui_alias,parent,export_props,allow_layer=True):
        # Only an exported render return, or a registered real layer sink with
        # a separately proven render opening control. Arbitrary returned
        # strings/unused helpers are not user-entry proofs.
        for ts in e.model.lists:
            if template not in ts:continue
            i=ts.index(template)
            if i and ts[i-1].value=='return':
                fn=e.model.enclosing_function(template.start)
                if fn and fn.symbol=='render' and fn.parent is parent and export_props.get('render')=='render':return ('render',fn)
            for call in e.model.calls:
                if call.tokens is not ts:continue
                if allow_layer and call.path in {('PlatformUI','openLayer'),('window','PlatformUI','openLayer'),((ui_alias or '__none__'),'openLayer')} and len(call.args)>=2 and call.args[1]==[template]:
                    self.real_ui_owner(e.model,call.path,call.start)
                    return ('layer',call)
                if call.path in {('PlatformUI','toolbar'),('window','PlatformUI','toolbar'),((ui_alias or '__none__'),'toolbar')} and any(arg==[template] for arg in call.args):
                    self.real_ui_owner(e.model,call.path,call.start)
                    outer=template.parent_template
                    if outer is not None:
                        return self.supported_template_context(e,outer,ui_alias,parent,export_props,allow_layer=allow_layer)
        return None

    def no_shadow(self,model,name,parent,use_positions,include_parent=False):
        for position in use_positions:
            scope=model.enclosing_function(position)
            while True:
                if scope is parent and not include_parent:break
                for ts in model.lists:
                    ms=matching(ts)
                    for i,t in enumerate(ts):
                        if scope is not None and t.start==scope.start:
                            j=i+1
                            while j<len(ts) and ts[j].start<scope.body_start and ts[j].value!='(':j+=1
                            if j<len(ts) and ts[j].value=='(' and any(x.value==name for x in ts[j+1:ms[j]]):raise Unsupported('UI helper/alias is shadowed by a function parameter')
                        if scope is not None and not(scope.body_start<t.start<scope.body_end):continue
                        if model.enclosing_function(t.start) is not scope:continue
                        if t.value=='function' and i+1<len(ts) and ts[i+1].value==name:raise Unsupported('UI helper/alias is shadowed by a nearer local declaration')
                if any(bound==name and binding_scope is scope for bound,position,binding_scope in model.variable_bindings()):raise Unsupported('UI helper/alias is shadowed by a nearer local declaration')
                if scope is parent:break
                if scope is None:raise Unsupported('Bound symbol is outside the original lexical scope')
                scope=scope.parent

    def real_ui_owner(self,model,path,position):
        if path[:1]==('PlatformUI',):self.no_shadow(model,'PlatformUI',None,[position],include_parent=True)
        elif path[:2]==('window','PlatformUI'):self.no_shadow(model,'window',None,[position],include_parent=True)

    def layer_opening(self,entry,e,callee,layer_call,parent,props,alias):
        opening=self.evidence(entry.get('opening_evidence'))
        if opening.model is not e.model:raise Unsupported('Layer opening control must be in the same registered render source')
        fn=e.model.enclosing_function(layer_call.start)
        if fn is None or fn.symbol is None or fn.parent is not parent:raise Unsupported('Layer opening needs a named same-scope original UI function')
        opener_callees=[callee[:-1]+(key,) for key,value in props.items() if value==fn.symbol]
        if not opener_callees:raise Unsupported('Layer UI function is not bound by the exact original static owner')
        for ts in e.model.lists:
            for token in ts:
                if token.kind!='template' or not opening.contains(token):continue
                html,expressions=template_markup(token);parser=HandlerParser()
                try:parser.feed(html);parser.close()
                except Exception:continue
                if not any(exact_handler(handler,opener,expressions,None) for event,handler in parser.handlers for opener in opener_callees):continue
                context=self.supported_template_context(opening,token,alias,parent,props,allow_layer=False)
                if context:
                    if alias:self.no_shadow(e.model,alias,parent,[token.start])
                    return True
        raise Unsupported('Layer has no exact opening control in the exported render template')

    def explicit_owner_export(self,e,link):
        mapping=link.get('global_owner_binding')
        if mapping is None:return self.owner_export(e,link.get('callee'),link.get('symbol'))
        if not isinstance(mapping,dict) or mapping.get('version')!='explicit-window-owner@1':raise Unsupported('Unsupported explicit global owner binding')
        bare=mapping.get('bare_owner');window=normalized_callee(mapping.get('window_owner'));callee=normalized_callee(link.get('callee'))
        if not isinstance(bare,str) or not IDENT.fullmatch(bare) or window!=('window',bare) or not callee or callee[:-1]!=(bare,):raise Unsupported('Explicit global owner binding differs from the exact callee')
        ge=self.evidence(mapping.get('evidence'))
        if ge.model is not e.model:raise Unsupported('Global owner binding uses a different published source')
        qualified='window.'+bare+'.'+callee[-1]
        binding=self.owner_export(e,qualified,link.get('symbol'));other=self.owner_export(ge,qualified,link.get('symbol'))
        if binding[3]!=other[3]:raise Unsupported('Global owner binding does not cover its exact unique publication')
        self.no_shadow(e.model,bare,None,[binding[3]],include_parent=True)
        return binding

    def p2_no_owner_writes(self,model,paths,allowed=()):
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts):
                if t.kind!='id'or i and ts[i-1].value in {'.','?.'}:continue
                p=path_at(ts,i,False,partial=True)
                if not p:continue
                actual,j=p
                match=any(actual[:len(root)]==root for root in paths)
                if not match and j<len(ts)and ts[j].value=='[':match=any(root[:len(actual)]==actual for root in paths)
                if not match:continue
                while j<len(ts)and ts[j].value in {'.','['}:j=ms[j]+1 if ts[j].value=='['else j+2
                if i and ts[i-1].value=='delete':raise Unsupported('Const alias/owner property is deleted or mutated')
                if j<len(ts)and ts[j].value in {'=','+=','-=','++','--'}and not(t in allowed and j==i+1):raise Unsupported('Const alias/owner is rebound or its property rewritten')
        for c in model.calls:
            if c.path not in {('Object','assign'),('Object','defineProperty'),('Object','defineProperties'),('Object','setPrototypeOf'),('Reflect','set'),('Reflect','deleteProperty') }or not c.args:continue
            p=path_at(c.args[0],0,False)
            if p and p[1]==len(c.args[0])and any(p[0][:len(root)]==root for root in paths):raise Unsupported('Const alias/owner is mutated by an unproven helper')

    def p2_direct_statement(self,body,node_start):
        ms=matching(body);at=0
        while at<len(body):
            begin=at
            while at<len(body)and body[at].value!=';':at=ms[at]+1 if body[at].value in {'(','[','{'}else at+1
            statement=body[begin:at+1]
            if any(t.start==node_start for t in statement):return bool(statement and statement[0].start==node_start)
            at+=1
        return False

    def p2_state_mutators(self,model,names,first,last,label='Selected member state'):
        for c in model.calls:
            if not(first<c.start<last)or c.path not in {('Object','assign'),('Object','defineProperty'),('Object','defineProperties'),('Object','setPrototypeOf'),('Reflect','set'),('Reflect','deleteProperty')}or not c.args:continue
            p=path_at(c.args[0],0,False,partial=True)
            if p and p[0][:1]in {(n,)for n in names}:raise Unsupported(label+' is mutated by an Object/Reflect helper')

    def p2_presence_guard(self,model,token):
        for ts in model.lists:
            at=next((i for i,t in enumerate(ts)if t is token),None)
            if at is None or at==0 or at+1>=len(ts)or ts[at-1].value!='!'or ts[at+1].value not in {'||','&&',')'}:continue
            ms=matching(ts)
            for i,t in enumerate(ts[:-1]):
                if t.value not in {'if','while'}or ts[i+1].value!='('or not(i+1<at<ms[i+1]):continue
                condition=ts[i+2:ms[i+1]]
                if any(t.value in {'=', '+=','-=','=>',',','?',':','return','yield'}for t in condition):continue
                if any(c.tokens is ts and c.start<token.start<c.end and c.path not in {('if',),('while',)}for c in model.calls):continue
                return True
        return False

    def p2_protected_escapes(self,model,names,first,last,allowed=(),label='Protected member state/capture'):
        for ts in model.lists:
            for i,t in enumerate(ts):
                if t.kind!='id'or t.value not in names or not(first<t.start<last)or i and ts[i-1].value in {'.','?.'}or t in allowed:continue
                p=path_at(ts,i,False)
                if not p or len(p[0])!=1:continue
                next_value=ts[i+1].value if i+1<len(ts)else None
                if next_value in {'=','+=','-=','++','--'}:continue
                if self.p2_presence_guard(model,t):continue
                raise Unsupported(label+' has an unproven bare-value use/copy/forward escape')
        for c in model.calls:
            if not(first<c.start<last):continue
            if c.path[:1]in {(n,)for n in names}:raise Unsupported(label+' invokes an unproven protected-object method')
            if any(len(a)==1 and a[0].kind=='id'and a[0].value in names and a[0]not in allowed for a in c.args):raise Unsupported(label+' escapes by whole-object forwarding to an unproven helper')

    def p2_const_alias(self,e,name,owner,parent,use,allowed_bare_uses=()):
        if not IDENT.fullmatch(name or ''):raise Unsupported('Const owner alias needs a bare identifier')
        expected=normalized_callee(owner)
        if not expected:raise Unsupported('Const owner alias needs an exact static receiver')
        found=[]
        for ts in e.model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts):
                if t.value!='const':continue
                j=i+1
                while j+1<len(ts) and ts[j].kind=='id' and ts[j+1].value=='=':
                    first=j;k=j+2
                    while k<len(ts) and ts[k].value not in {',',';'}:
                        k=ms[k]+1 if ts[k].value in {'(','[','{'} else k+1
                    rhs=ts[j+2:k];p=path_at(rhs,0,False)
                    if ts[first].value==name and p and p[0]==expected and p[1]==len(rhs) and e.model.scope(t.start)is parent and e.start<=t.start and ts[k-1].end<=e.end:found.append(ts[first])
                    if k>=len(ts) or ts[k].value!=',':break
                    j=k+1
        if len(found)!=1:raise Unsupported('Owner/helper alias lacks a unique same-scope immutable const binding')
        declaration=found[0]
        for ts in e.model.lists:
            for i,t in enumerate(ts[:-1]):
                if t.value==name and t is not declaration and (i==0 or ts[i-1].value not in {'.','?.'}) and ts[i+1].value in {'=','+=','-=','++','--'}:raise Unsupported('Const owner/helper alias is rebound or mutated')
        self.no_shadow(e.model,name,parent,[use])
        if expected[0]=='ui':self.alias(e,'ui',parent)
        else:self.no_shadow(e.model,expected[0],None,[declaration.start],include_parent=True)
        roots=[(name,),expected]
        if len(expected)==1:roots.append(('window',)+expected)
        self.p2_no_owner_writes(e.model,roots,[declaration])
        # An unproved copy can mutate the same object behind the protected
        # receiver. Reject escape rather than inventing a general alias graph.
        protected=[(name,),expected]
        if len(expected)==1:protected.append(('window',)+expected)
        for ts in e.model.lists:
            for i,t in enumerate(ts):
                if t.kind!='id'or i and ts[i-1].value in {'.','?.'}:continue
                p=path_at(ts,i,False)
                if not p or p[0]not in protected:continue
                end=p[1]
                if t is declaration or any(t is allowed for allowed in allowed_bare_uses):continue
                if i>=2 and ts[i-1].value=='='and ts[i-2]is declaration:continue
                if end<len(ts)and ts[end].value=='('and len(expected)>1:continue
                if end<len(ts)and ts[end].value in {'.','['}:continue
                if len(p[0])>1 and p[0][:1]==(name,)and p[0]!=expected:continue
                raise Unsupported('Protected const owner/helper alias escapes through an unproven copy or use')
        return expected

    def p2_escape(self,e,export):
        bound=self.owner_export(export,'window.PlatformUI.escape','escape');fn,params,body=self.p1_function(e,'escape',bound[1])
        if len(params)!=1:raise Unsupported('Real PlatformUI.escape lacks its original value parameter')
        value=params[0];v=[t.value for t in body]
        if len(body)<11 or v[:7]!=['return','String','(',value,'==','null','?'] or literal(body[7])!='' or v[8:11]!=[':',value,')']:raise Unsupported('Escape helper is not the bounded original String/null escape chain')
        self.no_shadow(e.model,'String',None,[fn.body_start+1],include_parent=True)
        pos=11
        for regex,entity in [('/&/g','&amp;'),('/</g','&lt;'),('/>/g','&gt;'),('/"/g','&quot;'),("/'/g",'&#039;')]:
            if pos+6>=len(body) or v[pos:pos+3]!=['.','replace','('] or body[pos+3].value!=regex or v[pos+4]!=',' or literal(body[pos+5])!=entity or v[pos+6]!=')':raise Unsupported('Escape helper differs from the original five ordered HTML replacements')
            pos+=7
        if v[pos:]!=[';']:raise Unsupported('Escape helper has an unproven wrapper or side effect')

    def p2_escaped_helper(self,op,parent,use,escape_use_validator=None):
        e=self.evidence(op.get('handler_helper_evidence'));call=op.get('helper_call',{})
        name=call.get('name');method=call.get('literal_method')
        if not IDENT.fullmatch(name or '') or not IDENT.fullmatch(method or ''):raise Unsupported('Opening helper needs an exact literal method')
        found=[]
        for ts in e.model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts):
                if [x.value for x in ts[i:i+4]]!=['const',name,'=','(']:continue
                close=ms[i+3];params=ts[i+4:close]
                if len(params)!=4 or params[0].kind!='id' or [x.value for x in params[1:3]]!=[',','...'] or params[3].kind!='id' or ts[close+1].value!='=>':continue
                at=close+2
                if at+4>=len(ts) or ts[at].kind!='id' or ts[at+1].value!='(' or ts[at+2].kind!='template' or ts[at+3].value!=')' or ts[at+4].value!=';':continue
                template=ts[at+2];parts=template.parts
                if [x[0]for x in parts]!=['text','expression','text','expression','text'] or [x.value for x in parts[1][1]]!=[params[0].value] or parts[2][1]!='(' or parts[4][1]!=')':continue
                owner=normalized_callee(parts[0][1].removesuffix('.'))
                if not owner or not parts[0][1].endswith('.'):continue
                args=parts[3][1];maps=[c for c in calls(args)if c.path==(params[3].value,'map')and c.index==0]
                if len(maps)!=1 or len(maps[0].args)!=1:continue
                mapper=maps[0].args[0]
                if len(mapper)!=8 or mapper[0].kind!='id' or [x.value for x in mapper[1:]]!=['=>','JSON','.','stringify','(',mapper[0].value,')']:continue
                suffix=args[maps[0].end_index+1:]
                if len(suffix)!=5 or [x.value for x in suffix[:3]]!=['.','join','('] or literal(suffix[3])!=',' or suffix[4].value!=')':continue
                if e.model.scope(t.start)is not parent or not(e.start<=t.start and ts[at+4].end<=e.end):continue
                allowed=escape_use_validator(ts[at].value) if escape_use_validator else ()
                self.p2_const_alias(self.evidence(op.get('escape_alias_evidence')),ts[at].value,'ui.escape',parent,use,allowed_bare_uses=allowed)
                self.no_shadow(e.model,'JSON',None,[mapper[2].start],include_parent=True);self.no_shadow(e.model,name,parent,[use])
                self.p2_no_owner_writes(e.model,[(name,)],[ts[i+1]])
                found.append(dict(name=name,method=method,owner=owner,parent=parent,model=e.model))
        if len(found)!=1:raise Unsupported('Opening helper is not the bounded escape/fixed-owner/JSON-arguments grammar')
        return found[0]

    def p2_table_rows(self,model,render,call,alias):
        arg=call.args[1];parsed=path_at(arg,0,False)
        if not parsed or not parsed[0]or not IDENT.fullmatch(parsed[0][0]):raise Unsupported('Inline table rows are a deterministic empty/unknown expression')
        name=parsed[0][0]
        if [t.value for t in arg]!=[name]:
            cs=calls(arg)
            if len(cs)!=1 or cs[0].path!=(name,'slice')or cs[0].index!=0 or cs[0].end_index!=len(arg)-1 or len(cs[0].args)!=2:raise Unsupported('Inline table rows have an unsupported or empty transformation')
            first,last=cs[0].args;v=[t.value for t in first];w=[t.value for t in last]
            normal=self.p1_slice_rows(arg,name,model,render.parent)
            if not normal and len(first)==7 and v[0]=='('and first[1].kind=='id'and v[2:6]==['-','1',')','*'] and first[6].kind=='number' and len(last)==3 and w[:2]==[first[1].value,'*']and last[2].value==first[6].value:
                try:normal=int(first[6].value)>0
                except ValueError:normal=False
            if not normal:raise Unsupported('Inline table rows slice is empty or outside the finite original pagination pattern')
        declarations=[]
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts[:-2]):
                if t.value!=name or not(render.body_start<t.start<call.start)or i and ts[i-1].value in {'.','?.'}:continue
                if ts[i+1].value=='=':
                    if i and ts[i-1].value in {'const','let'}:
                        if [x.value for x in ts[i+2:i+4]]==['[',']']:raise Unsupported('Inline table row declaration is deterministically empty')
                        declarations.append(t);continue
                    j=i+2
                    if [x.value for x in ts[j:j+6]]!=[alias,'.','sortByUpdated','?.','(',name]:raise Unsupported('Inline table rows are rewritten/cleared before its control')
                    end=ms[j+4]
                    if [x.value for x in ts[end+1:end+4]]!=['||',name,';']:raise Unsupported('Inline row sorting does not preserve original fallback')
                elif ts[i+1].value in {'.','['}:
                    j=i+1
                    while j<len(ts)and ts[j].value in {'.','['}:j=ms[j]+1 if ts[j].value=='['else j+2
                    if j<len(ts)and ts[j].value in {'=','+=','-=','++','--'}:raise Unsupported('Inline table rows are destructively mutated')
        if len(declarations)!=1:raise Unsupported('Inline table rows lack one original local query binding')
        if any(render.body_start<c.start<call.start and c.path[:1]==(name,)and c.path[1:]!=('slice',)for c in model.calls):raise Unsupported('Inline table rows use a destructive/unknown preprocessing call')

    def p2_table_event(self,render_e,e,opening_callee,binding,alias,op):
        _,parent,props,_=binding
        self.p2_const_alias(self.evidence(op.get('escape_alias_evidence')),alias,'PlatformUI',parent,render_e.start)
        if render_e.model is not e.model or props.get('render')!='render':raise Unsupported('Opening control lacks the same owner exported render')
        render,rp,rb=self.p1_function(render_e,'render',parent);templates=self.p1_returns(e.model,render,rb)
        if len(templates)!=1:raise Unsupported('Opening render lacks its one real returned UI template')
        # Keep original conditional guards but reject an unconditional earlier
        # return and raw/inert interpolation slots.
        template=templates[0];return_token=next(t for t in rb if t.value=='return' and t.end<=template.start and rb.index(t)+1<len(rb) and rb[rb.index(t)+1]is template)
        if not self.p2_direct_statement(rb,return_token.start):raise Unsupported('Inline table return is a dead/conditional rather than direct render statement')
        ms=matching(rb);guards=[]
        for i,t in enumerate(rb):
            if t.value!='if' or i+1>=len(rb) or rb[i+1].value!='(':continue
            close=ms[i+1];j=close+1;end=ms[j]if rb[j].value=='{'else next((k for k in range(j,len(rb))if rb[k].value==';'),len(rb)-1)
            if [x.value for x in rb[i+2:close]]==['true'] and any(x.value=='return'for x in rb[j:end+1]):raise Unsupported('Opening render is deterministically unreachable')
            guards.append((rb[j].start,rb[end].end))
        if any(t.value=='return' and t.start<return_token.start and e.model.enclosing_function(t.start)is render and not any(a<=t.start<=b for a,b in guards)for t in rb):raise Unsupported('Opening render has an unconditional earlier return')
        table_calls=[c for c in e.model.calls if c.path==(alias,'table') and e.model.enclosing_function(c.start)is render and any(c.tokens is part and c.index==0 and c.end_index==len(part)-1 and self.p1_active_part(template,part)for k,part in template.parts if k=='expression')]
        if len(table_calls)!=1 or len(table_calls[0].args)<2:raise Unsupported('Opening control is not in an actual active returned table')
        tc=table_calls[0];columns=tc.args[0]
        self.p2_table_rows(e.model,render,tc,alias)
        if not columns or columns[0].value!='['or matching(columns).get(0)!=len(columns)-1:raise Unsupported('Opening table needs literal columns, not an unused renderer')
        key=op.get('column_key_literal');index=op.get('event_object_argument_index')
        if not isinstance(key,str)or type(index)is not int or index<0:raise Unsupported('Opening object key/argument slot is missing')
        objects=[self.p1_props(x)for x in split_tokens(columns[1:-1])if x];chosen=[x for x in objects if self.p1_literal(x.get('key',[]))==key]
        if len(chosen)!=1:raise Unsupported('Opening table object key differs or is duplicated')
        renderer=chosen[0].get('render',[]);rm=matching(renderer)
        if not renderer or renderer[0].value!='(':raise Unsupported('Opening column renderer is not an exact arrow')
        close=rm[0];ps=split_tokens(renderer[1:close])
        if any(len(p)!=1 or p[0].kind!='id'for p in ps)or [x.value for x in renderer[close+1:close+2]]!=['=>']or len(renderer)!=close+3 or renderer[-1].kind!='template':raise Unsupported('Opening column renderer has an unsupported body')
        rt=renderer[-1]
        if not e.contains(rt):raise Unsupported('Table control quote does not cover its exact literal renderer')
        helper=self.p2_escaped_helper(op,parent,rt.start)
        if helper['model']is not e.model or helper['owner']+(helper['method'],)!=opening_callee:raise Unsupported('Opening helper literal owner/method differs from members control')
        html,expressions=template_markup(rt);parser=TableHTMLContext(expressions);parser.feed(html);parser.close();valid=[]
        for ev,h in parser.handlers:
            if ev!='click'or not exact_handler(h,opening_callee,expressions,helper):continue
            tokens=expressions.get(h.strip(),[]);cs=calls(tokens)
            if len(cs)!=1 or len(cs[0].args)<=index+1:continue
            arg=cs[0].args[index+1];values=[t.value for t in arg];params=[p[0].value for p in ps]
            if values==[params[0]]or len(params)>1 and (values==[params[1],'.',key]or len(arg)==4 and values[:2]==[params[1],'[']and literal(arg[2])==key and values[-1]==']'):valid.append(h)
        if not valid:raise Unsupported('Opening helper control does not bind the exact row object')
        table=self.evidence(op.get('table_implementation_evidence'));export=self.evidence(op.get('table_export_evidence'));escape=self.evidence(op.get('escape_implementation_evidence'))
        if not(table.model is export.model is escape.model):raise Unsupported('Table/escape library provenance is disconnected')
        self.p1_table_contract(table,export,key);self.p2_escape(escape,export)
        self.no_shadow(e.model,opening_callee[0],None,[rt.start],include_parent=True)
        return rt

    def p2_direct_layer(self,entry,e,callee,binding,alias):
        op=entry.get('opening_provenance',{})
        if not isinstance(op,dict) or op.get('version')!='bounded-direct-table-layer@1' or not alias:raise Unsupported('Unsupported explicit opening/save layer provenance')
        if op.get('event_object_argument_index')!=0:raise Unsupported('Member opening profile binds the first object argument only')
        save,parent,props,_=binding;name=op.get('opening_symbol');opening_callee=normalized_callee(op.get('opening_callee'))
        if not IDENT.fullmatch(name or '') or opening_callee!=callee[:-1]+(name,) or props.get(name)!=name:raise Unsupported('Layer opening method differs from the exact exported owner')
        he=self.evidence(op.get('opening_handler_evidence'));opening,parameters,body=self.p1_function(he,name,parent)
        if he.model is not e.model or not parameters:raise Unsupported('Layer opening handler has a different lexical source')
        layers=[c for c in e.model.calls if c.path==(alias,'openLayer') and e.model.enclosing_function(c.start)is opening and len(c.args)>=2 and len(c.args[1])==1 and c.args[1][0].kind=='template' and e.contains(c.args[1][0])]
        hits=[]
        for layer in layers:
            template=layer.args[1][0];html,expressions=template_markup(template);parser=TableHTMLContext(expressions);parser.feed(html);parser.close()
            if any(ev==entry['event'] and exact_handler(h,callee,expressions,None) for ev,h in parser.handlers):hits.append(layer)
        if len(hits)!=1:raise Unsupported('Actual layer lacks its independent exact save event/control')
        if not self.p2_direct_statement(body,hits[0].start):raise Unsupported('Actual save layer is in a dead/conditional opening statement')
        render_e=self.evidence(entry.get('opening_evidence'))
        self.p2_table_event(render_e,render_e,opening_callee,binding,alias,op)
        self.no_shadow(e.model,callee[0],None,[opening.start,save.start],include_parent=True)
        self._p2_opening=dict(fn=opening,params=parameters,model=e.model,layer=hits[0],op=op)
        return e,callee

    def p2_state_callsite(self,link,e,current_fn):
        spec=link.get('state_branch',{});opening=getattr(self,'_p2_opening',None)
        if not opening or not isinstance(spec,dict)or spec.get('version')!='bounded-state-else@1' or link.get('reachability')!='conditional_only':raise Unsupported('Member branch needs its independently proven opening and conditional_only reachability')
        for key in ('state_symbol','capture_symbol','kind_field','result_variable','object_field'):
            if not IDENT.fullmatch(spec.get(key,'')):raise Unsupported('Member branch has an invalid state/result binding')
        selected=spec.get('selected_value');excluded=spec.get('excluded_values');index=spec.get('object_argument_index')
        if not isinstance(selected,str)or not selected or not isinstance(excluded,list)or not excluded or any(not isinstance(x,str)for x in excluded)or selected in excluded or type(index)is not int or index!=0:raise Unsupported('Selected member kind/branch exclusions/first object argument are invalid')
        ce=self.evidence(spec.get('capture_evidence'))
        if not(ce.model is e.model is opening['model'])or not ce.contains(opening['fn']):raise Unsupported('Member branch capture differs from the proven opening handler')
        state=spec['state_symbol'];capture=spec['capture_symbol'];field=spec['kind_field'];result=spec['result_variable'];object_field=spec['object_field'];model=e.model
        ofn,oparams,obody=self.p1_function(ce,opening['fn'].symbol,current_fn.parent);captures=[]
        for ts in model.lists:
            ms=matching(ts)
            for i,t in enumerate(ts[:-2]):
                if t.value!=state or [x.value for x in ts[i+1:i+3]]!=['=','{']or model.enclosing_function(t.start)is not ofn:continue
                end=ms[i+2];props={}
                for p in split_tokens(ts[i+3:end]):
                    if not p:continue
                    key=p[0].value if p[0].kind=='id'else literal(p[0])
                    if key is None or key in props:raise Unsupported('Member state capture has a dynamic/duplicate property')
                    if len(p)==1 and p[0].kind=='id':props[key]=p
                    elif len(p)>2 and p[1].value==':':props[key]=p[2:]
                    else:raise Unsupported('Member state capture has an unsupported property')
                if self.p1_literal(props.get(field,[]))!=selected or [x.value for x in props.get(object_field,[])]!=[oparams[0]]:raise Unsupported('Opening does not capture the selected member kind and original object identity')
                if ts[end].end>=opening['layer'].start:raise Unsupported('Member state is not captured before the actual layer')
                captures.append((t,ts[end].end))
        if len(captures)!=1:raise Unsupported('Opening has no unique selected member state capture')
        token,captured_end=captures[0];om=matching(obody);at=0;direct=False
        while at<len(obody):
            begin=at
            while at<len(obody)and obody[at].value!=';':at=om[at]+1 if obody[at].value in {'(','[','{'}else at+1
            statement=obody[begin:at+1]
            if any(t is token for t in statement):direct=bool(statement and statement[0]is token)
            at+=1
        if not direct:raise Unsupported('Selected member state is not a direct opening-handler capture')
        self.p2_state_mutators(model,{state},captured_end,opening['layer'].start)
        self.p2_protected_escapes(model,{state},captured_end,opening['layer'].start,label='Opening selected state')
        for ts in model.lists:
            mm=matching(ts)
            for k,t in enumerate(ts[:-1]):
                if t.value!=state or not(captured_end<t.start<opening['layer'].start)or k and ts[k-1].value in {'.','?.'}:continue
                j=k+1
                while j<len(ts)and ts[j].value in {'.','['}:j=mm[j]+1 if ts[j].value=='['else j+2
                if j<len(ts)and ts[j].value in {'=','+=','-=','++','--'}:raise Unsupported('Opening selected member state is overwritten before the save layer')
        f,params,body=self.p1_function(e,current_fn.symbol,current_fn.parent)
        if f is not current_fn:raise Unsupported('Member save function differs from the current source chain')
        declarations=[t for i,t in enumerate(body[:-2])if [x.value for x in body[i:i+3]]==['let',result,';']and model.enclosing_function(t.start)is f]
        bound=[t for i,t in enumerate(body[:-4])if [x.value for x in body[i:i+5]]==['const',capture,'=',state,';']and model.enclosing_function(t.start)is f]
        if len(declarations)!=1 or len(bound)!=1:raise Unsupported('Member result or immutable state capture is not declared in the save function')
        callee=normalized_callee(link.get('callee'));hits=[c for c in model.calls if e.contains(c) and c.path==callee and model.enclosing_function(c.start)is current_fn]
        if len(hits)!=1:raise Unsupported('Member branch lacks one actual same-scope invocation')
        call=hits[0]
        if bound[0].start>=call.start or declarations[0].start>=call.start:raise Unsupported('Member state/result declaration is after the invocation')
        self.p2_state_mutators(model,{state,capture},f.body_start,call.start)
        binding_index=body.index(bound[0]);original_capture_value=body[binding_index+3]
        self.p2_protected_escapes(model,{state,capture},f.body_start,call.start,[original_capture_value],label='Selected save state/capture')
        self.no_shadow(model,state,current_fn.parent,[bound[0].start]);self.no_shadow(model,capture,current_fn,[call.start])
        # Exact source if/else-if chain, not a claimed list of paths.
        bm=matching(body);branches=[]
        for i,t in enumerate(body):
            if t.value!='if'or i and body[i-1].value=='else'or model.enclosing_function(t.start)is not f or i+1>=len(body)or body[i+1].value!='(':continue
            at=i;values=[];final=None
            while True:
                if body[at].value!='if'or body[at+1].value!='(':break
                close=bm[at+1];condition=body[at+2:close]
                if len(condition)!=5 or [x.value for x in condition[:4]]!=[capture,'.',field,'===']or literal(condition[4])is None:break
                j=close+1
                if body[j].value!='{':break
                end=bm[j];values.append(literal(condition[4]));next_at=end+1
                if next_at>=len(body)or body[next_at].value!='else':break
                next_at+=1
                if body[next_at].value=='if':at=next_at;continue
                if body[next_at].value=='{':final=(next_at,bm[next_at]);break
                break
            if final and body[final[0]].start<call.start<body[final[1]].end:branches.append((values,final))
        if len(branches)!=1 or branches[0][0]!=excluded:raise Unsupported('Member branch omits or changes its preceding if/else-if exclusions')
        start,end=branches[0][1];segment=body[start+1:end];sm=matching(segment);statements=[];i=0
        while i<len(segment):
            begin=i
            while i<len(segment)and segment[i].value!=';':
                i=sm[i]+1 if segment[i].value in {'(','[','{'}else i+1
            if i<len(segment):statements.append(segment[begin:i+1]);i+=1
            elif begin<len(segment):raise Unsupported('Member branch has an unsupported incomplete statement')
        matching_statements=[s for s in statements if any(t is call.tokens[call.index]for t in s)]
        if len(matching_statements)!=1:raise Unsupported('Member invocation is deferred/nested or not a direct selected-branch statement')
        statement=matching_statements[0]
        if len(statement)<4 or [x.value for x in statement[:2]]!=[result,'=']or statement[2].start!=call.start or statement[-1].value!=';'or statement[-2].end!=call.end:raise Unsupported('Member branch does not directly assign its exact invocation to declared result')
        if len(call.args)<=index or [x.value for x in call.args[index]]!=[capture,'.',object_field]:raise Unsupported('Member invocation uses a different captured object identity')
        # Protect the selected result and shared feedback tail. Earlier
        # create/edit assignments remain distinct mutually exclusive branches.
        for ts in model.lists:
            mm=matching(ts)
            for k,t in enumerate(ts[:-1]):
                if t.value!=result or not(call.end<t.start<f.body_end)or k and ts[k-1].value in {'.','?.'}:continue
                j=k+1
                while j<len(ts)and ts[j].value in {'.','['}:j=mm[j]+1 if ts[j].value=='['else j+2
                if j<len(ts)and ts[j].value in {'=','+=','-=','++','--'}or k and ts[k-1].value=='delete':raise Unsupported('Selected member result is overwritten or its property rewritten after invocation')
        self.p2_state_mutators(model,{result},call.end,f.body_end,label='Selected member result')
        self.p2_protected_escapes(model,{result},call.end,f.body_end,label='Selected member result')
        # Before the call, neither selected state nor its immutable alias may
        # be deterministically replaced or have a property rewritten.
        for ts in model.lists:
            mm=matching(ts)
            for k,t in enumerate(ts[:-1]):
                if t.value not in {state,capture}or not(f.body_start<t.start<call.start)or k and ts[k-1].value in {'.','?.'}:continue
                j=k+1
                while j<len(ts)and ts[j].value in {'.','['}:j=mm[j]+1 if ts[j].value=='['else j+2
                if j<len(ts)and ts[j].value in {'=','+=','-=','++','--'}:
                    if t.value==capture and k and ts[k-1].value=='const'and j==k+1:continue
                    raise Unsupported('Selected member state/capture is rewritten before its call')
        return call

    def entry(self,entry,owner_binding):
        if entry.get('kind')!='template_event' or entry.get('event') not in EVENTS:raise Unsupported('Source-chain entry must be a supported actual UI template event')
        e=self.evidence(entry.get('evidence'));callee=normalized_callee(entry.get('callee'))
        if not callee:raise Unsupported('Source-chain entry needs an exact static callee')
        fn,parent,props,_=owner_binding
        helper=self.helper(self.evidence(entry['handler_helper_evidence']),entry.get('helper_call')) if 'handler_helper_evidence' in entry else None
        if helper and (helper['model'] is not e.model or helper['parent'] is not parent):raise Unsupported('UI helper is outside the exact source lexical scope')
        alias=None
        if 'ui_alias_evidence' in entry:
            a=self.evidence(entry['ui_alias_evidence'])
            if a.model is not e.model:raise Unsupported('UI sink alias is from a different source file')
            alias=self.alias(a,'ui',parent)
        if 'raw_table_control_provenance' in entry:
            return self.p4_returned_raw_table(entry,e,callee,owner_binding,alias)
        if 'quoted_control_provenance' in entry:
            return self.p4_quoted_return_control(entry,e,callee,owner_binding,alias)
        if 'direct_table_provenance' in entry:
            op=entry['direct_table_provenance']
            if not isinstance(op,dict)or op.get('version')!='bounded-direct-table-event@1'or not alias or 'opening_provenance'in entry or 'render_provenance'in entry:raise Unsupported('Unsupported direct table event provenance')
            self.p2_table_event(self.evidence(op.get('render_evidence')),e,callee,owner_binding,alias,op)
            return e,callee
        if 'opening_provenance' in entry:
            if 'render_provenance'in entry:raise Unsupported('Independent layer save cannot replace its opening by another entry mode')
            return self.p2_direct_layer(entry,e,callee,owner_binding,alias)
        if 'render_provenance' in entry:
            self.table_return_provenance(entry,e,callee,owner_binding,alias)
            return e,callee
        hits=[]
        for ts in e.model.lists:
            for t in ts:
                if t.kind!='template' or not e.contains(t):continue
                html,exprs=template_markup(t);parser=HandlerParser()
                try:parser.feed(html);parser.close()
                except Exception:continue
                if not any(ev==entry['event'] and exact_handler(h,callee,exprs,helper) for ev,h in parser.handlers):continue
                context=self.supported_template_context(e,t,alias,parent,props)
                if context:
                    if context[0]=='layer':self.layer_opening(entry,e,callee,context[1],parent,props,alias)
                    if alias:self.no_shadow(e.model,alias,parent,[t.start])
                    if helper:self.no_shadow(e.model,helper['name'],parent,[t.start])
                    hits.append(t)
        if not hits:raise Unsupported('UI entry lacks exact real event in supported render/layer/toolbar template grammar')
        return e,callee
    def validate(self):
        chain=self.proof.get('source_chain',{})
        if isinstance(chain,dict)and chain.get('version')=='bounded-active-source-chain@1':
            self.validate_active_flow();return []
        if not isinstance(chain,dict) or chain.get('version')!='bounded-static-chain@1':raise Unsupported('Unsupported bounded source-chain version')
        links=chain.get('links')
        if not isinstance(links,list) or not 1<=len(links)<=8:raise Unsupported('Source chain requires one to eight explicit source bindings')
        first=links[0]
        if chain.get('entry',{}).get('opening_provenance')and isinstance(first,dict)and normalized_callee(first.get('callee'))!=normalized_callee(chain.get('entry',{}).get('callee')):raise Unsupported('Independent save entry/callee differs from its exact owner export')
        if not isinstance(first,dict) or first.get('kind')!='static_owner_export' or normalized_callee(first.get('callee'))!=normalized_callee(chain.get('entry',{}).get('callee')):raise Unsupported('UI source chain must start with its exact owner export')
        first_e=self.evidence(first.get('evidence'));binding=self.explicit_owner_export(first_e,first)
        entry_e,current_callee=self.entry(chain.get('entry',{}),binding)
        if entry_e.model is not first_e.model:raise Unsupported('UI entry and first static owner export must share their original source file')
        current_fn=binding[0];current_model=first_e.model;pending=None
        for link in links[1:]:
            if not isinstance(link,dict):raise Unsupported('Invalid source chain link')
            kind=link.get('kind');e=self.evidence(link.get('evidence'))
            if kind=='callsite':
                if pending is not None or e.model is not current_model or link.get('from_symbol')!=current_fn.symbol:raise Unsupported('Callsite is disconnected from current source implementation')
                callee=normalized_callee(link.get('callee'))
                if link.get('branch')=='state_else':
                    self.p2_state_callsite(link,e,current_fn);pending=callee;current_callee=callee;continue
                hits=[c for c in e.model.calls if e.contains(c) and c.path==callee and e.model.enclosing_function(c.start) is current_fn]
                if len(hits)!=1:raise Unsupported('Callsite lacks one actual invocation in the exact current named function')
                if callee==(current_fn.symbol,):raise Unsupported('A self-call/confirmation recursion cannot initiate a source operation')
                c=hits[0];branch=link.get('branch')
                if branch in {'consequent','alternate'}:
                    if link.get('reachability')!='conditional_only' or not IDENT.fullmatch(link.get('branch_condition','')):raise Unsupported('Conditional source call must preserve its exact branch/reachability')
                    # Exactly const result = condition ? CALL : CALL; bounded
                    # ternary. Each selected operand must be the complete call.
                    ts=[t for t in c.tokens if e.start<=t.start and t.end<=e.end]
                    if ts and ts[-1].value==';':ts=ts[:-1]
                    prefix=3 if len(ts)>3 and ts[0].value in {'const','let','var'} and ts[1].kind=='id' and ts[2].value=='=' else 0
                    ts=ts[prefix:]
                    if len(ts)<5 or ts[0].value!=link['branch_condition'] or ts[1].value!='?':raise Unsupported('Conditional call is outside the bounded ternary grammar')
                    ms=matching(ts);colon=None;i=2
                    while i<len(ts):
                        if ts[i].value in {'(','[','{'}:i=ms[i]+1;continue
                        if ts[i].value==':':colon=i;break
                        i+=1
                    operand=ts[2:colon] if branch=='consequent' and colon is not None else ts[colon+1:] if colon is not None else []
                    if not operand or operand[0].start!=c.start or operand[-1].end!=c.end:raise Unsupported('Call is not the declared conditional operand')
                elif branch=='unconditional':
                    ts=[t for t in c.tokens if e.start<=t.start and t.end<=e.end]
                    if ts and ts[-1].value==';':ts=ts[:-1]
                    if ts and ts[0].value=='return':ts=ts[1:]
                    elif len(ts)>3 and ts[0].value in {'const','let','var'} and ts[1].kind=='id' and ts[2].value=='=':ts=ts[3:]
                    if not ts or ts[0].start!=c.start or ts[-1].end!=c.end:raise Unsupported('Unconditional callsite must be one complete direct call statement')
                else:raise Unsupported('Callsite must explicitly preserve unconditional or conditional branch')
                pending=callee;current_callee=callee
                if callee and len(callee)==1:
                    self.no_shadow(current_model,callee[0],current_fn.parent,[c.start])
                    fn=current_model.declaration(callee[0],current_fn.parent)
                    if fn is None:raise Unsupported('Bare call target lacks unique same-scope implementation')
                    current_fn=fn;pending=None
            elif kind=='const_owner_alias':
                alias=link.get('alias');owner=normalized_callee(link.get('owner'))
                if not pending or pending[0]!=alias or not owner or len(owner)!=1 or e.model is not current_model:raise Unsupported('Const owner alias does not bind the exact pending receiver')
                self.p2_const_alias(e,alias,link.get('owner'),current_fn.parent,current_fn.body_start+1)
                pending=owner+pending[1:];current_callee=pending
            elif kind=='factory_owner':
                factory=link.get('factory_symbol');owner=normalized_callee(link.get('owner'))
                if not pending or pending[0]!=str(factory)+'()' or not owner or e.model is not current_model:raise Unsupported('Factory owner does not bind the exact pending member call')
                found=[]
                for ts in e.model.lists:
                    ms=matching(ts)
                    for i in range(len(ts)-6):
                        if [t.value for t in ts[i:i+6]]!=['const',factory,'=','(',')','=>']:continue
                        result=path_at(ts,i+6,False)
                        if not result or result[0]!=owner:continue
                        end=result[1]
                        if end<len(ts) and ts[end].value!=';':continue
                        if not(e.start<=ts[i].start and ts[end-1].end<=e.end) or e.model.scope(ts[i].start) is not current_fn.parent:continue
                        found.append(ts[i])
                if len(found)!=1:raise Unsupported('Factory owner requires one same-scope actual const arrow returning only that static owner')
                self.no_shadow(current_model,factory,current_fn.parent,[current_fn.body_start+1])
                self.no_shadow(current_model,owner[0],None,[found[0].start],include_parent=True)
                current_callee=owner+pending[1:];pending=current_callee
            elif kind=='static_owner_export':
                if pending is None or normalized_callee(link.get('callee'))!=pending:raise Unsupported('Static export is not the exact pending call receiver/property')
                b=self.explicit_owner_export(e,link)
                current_fn=b[0];current_model=e.model;pending=None
            else:raise Unsupported('Unsupported source chain link kind: '+str(kind))
        terminal=chain.get('terminal',{});te=self.evidence(terminal.get('evidence'))
        if pending is not None:raise Unsupported('Unresolved member/factory call remains in the source chain')
        if terminal.get('symbol')!=self.proof.get('target_symbol') or terminal.get('symbol')!=current_fn.symbol or te.model is not current_model or te.model.path!=self.candidate_path or not te.contains(current_fn):raise Unsupported('Source-chain terminal does not bind the exact original candidate implementation')
        return []


def source_chain_errors(review,proof,record,scope_review,candidate_path):
    try:return ChainReview(review,proof,record,scope_review,candidate_path).validate()
    except (Unsupported,TypeError,KeyError,ValueError,AttributeError,RecursionError,OSError) as exc:
        return ['Bounded source-chain proof pending: '+str(exc)]


# Append-only proposal for bounded_source_chain.py. No source JS is executed.
# Only the explicit readonly-result role below is implemented. Other roles stay pending.

def _p4_anchor_function(model, symbol):
    functions = [f for f in model.functions if f.is_declaration and f.symbol == symbol]
    if len(functions) != 1:
        raise Unsupported('P4 readonly anchor needs one unique original named parent')
    fn = functions[0]
    for ts in model.lists:
        for i, token in enumerate(ts):
            if token.start != fn.start or token.value != 'function':
                continue
            ms = matching(ts); first = i + 2
            if ts[first].value != '(':
                raise Unsupported('P4 original function parameters are unsupported')
            close = ms[first]; params = split_tokens(ts[first + 1:close])
            if params == [[]]:
                params = []
            if any(not p or p[0].kind != 'id' or len(p) > 1 and (len(p) != 3 or p[1].value != '=') for p in params):
                raise Unsupported('P4 readonly role only accepts named parameters with literal defaults')
            for param in params:
                if len(param) == 3 and not (param[2].kind == 'string' and literal(param[2]) is not None or param[2].value in {'true', 'false', 'null'} or param[2].kind == 'number' and re.fullmatch(r'(?:0|[1-9][0-9]*)(?:\.[0-9]+)?', param[2].value)):
                    raise Unsupported('P4 readonly default is not one supported primitive literal')
            return fn, params, ts[close + 2:ms[close + 1]]
    raise Unsupported('P4 original parent body is unavailable')


def _p4_anchor_binding(model, fn, name, evidence):
    found = []
    for ts in model.lists:
        ms = matching(ts)
        for i, token in enumerate(ts):
            if token.value != 'const' or model.enclosing_function(token.start) is not fn:
                continue
            direct_body = _p4_anchor_function(model, fn.symbol)[2]
            if not ChainReview.p2_direct_statement(None, direct_body, token.start):
                continue
            end = i + 1
            while end < len(ts) and ts[end].value != ';':
                end = ms[end] + 1 if ts[end].value in {'(', '[', '{'} else end + 1
            for part in split_tokens(ts[i + 1:end]):
                if len(part) >= 3 and part[0].value == name and part[1].value == '=':
                    if evidence.start <= part[0].start and part[-1].end <= evidence.end:
                        found.append((part[0], part[2:]))
    if len(found) != 1:
        raise Unsupported('P4 readonly/result binding is not one exact original const: ' + str(name))
    return found[0]


def _p4_anchor_no_writes(engine, model, names, first, last, allowed=()):
    # allowed contains only actual tokens independently found and checked by
    # this role validator: readonly/result/current consts and dialog=current.
    engine.p4_no_write_contexts(model, names, first, last, allowed)
    engine.p2_state_mutators(model, set(names), first, last,
                             label='P4 readonly identity/result/capture')


def _p4_readonly_result_role(engine, contract, context):
    op = contract.get('role_evidence')
    if not isinstance(op, dict) or op.get('version') != 'bounded-readonly-result-expression@1':
        raise Unsupported('P4 readonly-result evidence version is unsupported')
    names = ['containing_symbol', 'result_symbol', 'readonly_symbol', 'identity_parameter',
             'readonly_parameter', 'ui_alias', 'access_factory_symbol', 'access_method',
             'dialog_symbol', 'dialog_capture_symbol', 'save_symbol']
    if any(not isinstance(op.get(key), str) or not IDENT.fullmatch(op[key]) for key in names):
        raise Unsupported('P4 readonly-result needs exact static original names')
    if op['containing_symbol'] != context['implementation_symbol']:
        raise Unsupported('P4 readonly expression is assigned to another parent')
    expr = engine.evidence(op.get('expression_evidence')); model = expr.model
    if model.path != engine.candidate_path or str(model.path) != context['source_file'] or expr.record.get('source_sha256') != context['source_sha256']:
        raise Unsupported('P4 readonly expression moved from its original candidate file/SHA')
    fn, params, body = _p4_anchor_function(model, op['containing_symbol'])
    result_token, result_rhs = _p4_anchor_binding(model, fn, op['result_symbol'], expr)
    if not context['source_line_start'] <= model.source.count('\n', 0, result_token.start) + 1 <= context['source_line_end']:
        raise Unsupported('P4 result binding is outside the original locator line')
    if model.enclosing_function(result_token.start) is not fn:
        raise Unsupported('P4 result expression lacks its unique lexical parent')
    result, readonly, ident = op['result_symbol'], op['readonly_symbol'], op['identity_parameter']
    locator = lex(context.get('source_locator', ''))
    if len(locator) != 6 or [t.value for t in locator[:5]] != ['const', result, '=', readonly, '?'] or locator[5].kind != 'string' or literal(locator[5]) != op.get('detail_literal'):
        raise Unsupported('P4 role result is not the exact original anchored readonly expression')
    values = [t.value for t in result_rhs]
    if len(result_rhs) != 9 or values[0:2] != [readonly, '?'] or values[3:6] != [':', ident, '?'] or values[7] != ':' or any(result_rhs[n].kind != 'string' or literal(result_rhs[n]) is None for n in [2, 6, 8]) or literal(result_rhs[2]) != op.get('detail_literal'):
        raise Unsupported('P4 anchor is not the original readonly/detail versus edit/new literal expression')
    ri, ii = op.get('readonly_argument_index'), op.get('identity_argument_index')
    if type(ri) is not int or type(ii) is not int or ri == ii or not 0 <= ri < len(params) or not 0 <= ii < len(params) or params[ri][0].value != op['readonly_parameter'] or params[ii][0].value != ident:
        raise Unsupported('P4 readonly/identity argument slots differ from the original function')
    if len(params[ri]) == 3 and params[ri][2].value != 'false':
        raise Unsupported('P4 readonly parameter has an unsupported default')
    read_e = engine.evidence(op.get('readonly_binding_evidence'))
    if read_e.model is not model:
        raise Unsupported('P4 readonly condition is from another file')
    readonly_token, readonly_rhs = _p4_anchor_binding(model, fn, readonly, read_e)
    expected = ['!', '!', ident, '&&', '(', op['readonly_parameter'], '||', '!', op['access_factory_symbol'], '(', ')', '.', op['access_method'], '(', 'true', ')', ')']
    if [t.value for t in readonly_rhs] != expected:
        raise Unsupported('P4 readonly source is not the finite original identity/readOnly guard')
    if not readonly_rhs[-1].end < result_token.start:
        raise Unsupported('P4 readonly value is not established before its result expression')
    layer_e = engine.evidence(op.get('layer_evidence'))
    if layer_e.model is not model:
        raise Unsupported('P4 readonly result consumer is disconnected')
    engine.p2_const_alias(engine.evidence(op.get('ui_alias_evidence')), op['ui_alias'], 'PlatformUI', fn.parent, layer_e.start)
    alias = op['ui_alias']
    layers = [c for c in model.calls if layer_e.contains(c) and c.path == (alias, 'openLayer') and model.enclosing_function(c.start) is fn and len(c.args) >= 2 and len(c.args[1]) == 1 and c.args[1][0].kind == 'template']
    if len(layers) != 1 or len(layers[0].args[0]) != 1 or literal(layers[0].args[0][0]) != op.get('layer_id_literal'):
        raise Unsupported('P4 result lacks one exact real original layer sink')
    layer = layers[0]; template = layer.args[1][0]
    if not engine.p2_direct_statement(body, layer.start):
        raise Unsupported('P4 readonly result layer is not a direct original statement')
    guarded = []; bm = matching(body)
    for i, token in enumerate(body):
        if token.value != 'if' or i + 1 >= len(body) or body[i+1].value != '(':
            continue
        close = bm[i+1]; start = close + 1
        end = bm[start] if body[start].value == '{' else next((j for j in range(start, len(body)) if body[j].value == ';'), len(body)-1)
        if [t.value for t in body[i+2:close]] == ['true'] and body[start].start < layer.start and any(t.value == 'return' for t in body[start:end+1]):
            raise Unsupported('P4 readonly result is deterministically unreachable')
        guarded.append((body[start].start, body[end].end))
    if any(t.value == 'return' and t.start < layer.start and model.enclosing_function(t.start) is fn and not any(a <= t.start <= b for a,b in guarded) for t in body):
        raise Unsupported('P4 an unconditional return disconnects the readonly result')
    expressions = [part for kind, part in template.parts if kind == 'expression']
    index, save_index = op.get('result_expression_index'), op.get('no_save_expression_index')
    if type(index) is not int or index != 0 or not 0 <= index < len(expressions) or [t.value for t in expressions[index]] != [result] or not engine.p1_active_part(template, expressions[index]):
        raise Unsupported('P4 original result is not the first active HTML text interpolation')
    html, _ = template_markup(template); balanced = P3BalancedHTML(); balanced.feed(html); balanced.close()
    if not balanced.valid or balanced.stack:
        raise Unsupported('P4 readonly layer has an unbalanced/inert ancestor context')
    if type(save_index) is not int or not 0 <= save_index < len(expressions) or not engine.p1_active_part(template, expressions[save_index]):
        raise Unsupported('P4 readonly save exclusion is not an active original text slot')
    branch = expressions[save_index]
    if len(branch) != 5 or [t.value for t in branch[:2]] != [readonly, '?'] or branch[2].kind != 'string' or literal(branch[2]) != '' or branch[3].value != ':' or branch[4].kind != 'template':
        raise Unsupported('P4 readonly true branch must exclude the whole save control')
    helper_op = dict(op); helper_op['helper_call'] = dict(op.get('helper_call', {}))
    helper_op['helper_call']['literal_method'] = op['save_symbol']
    save_helper = engine.p2_escaped_helper(helper_op, fn.parent, template.start)
    escape = engine.evidence(op.get('escape_implementation_evidence')); export = engine.evidence(op.get('escape_export_evidence'))
    if escape.model is not export.model:
        raise Unsupported('P4 real escaping implementation/export are disconnected')
    engine.p2_escape(escape, export)
    save_callee = save_helper['owner'] + (op['save_symbol'],)
    save_template = branch[4]; sh, se = template_markup(save_template); parser = TableHTMLContext(se); parser.feed(sh); parser.close()
    if not any(event == 'click' and exact_handler(handler, save_callee, se, save_helper) for event, handler in parser.handlers):
        raise Unsupported('P4 excluded branch does not contain the original save control')
    save_calls = [c for c in model.calls if template.start < c.start < template.end and c.path == (save_helper['name'],) and c.args and len(c.args[0]) == 1 and literal(c.args[0][0]) == op['save_symbol']]
    if len(save_calls) != 1 or not save_template.start < save_calls[0].start < save_template.end:
        raise Unsupported('P4 another save invocation survives outside the readonly exclusion')
    save_controls = []
    for ts in model.lists:
        for token in ts:
            if token.kind != 'template' or not template.start <= token.start <= token.end <= template.end:
                continue
            text, parts = template_markup(token); hp = TableHTMLContext(parts); hp.feed(text); hp.close()
            if any(event == 'click' and exact_handler(handler, save_callee, parts, save_helper) for event, handler in hp.handlers):
                save_controls.append(token)
    if save_controls != [save_template]:
        raise Unsupported('P4 a raw/helper save control remains outside the excluded branch')
    # The same narrow entry quote must contain exactly the original readOnly=true
    # event. Normal source_chain_errors must still prove its actual UI reachability.
    entry = engine.proof.get('source_chain', {}).get('entry', {})
    entry_e = engine.evidence(entry.get('evidence')); call_e = engine.evidence(op.get('readonly_call_evidence'))
    if entry_e.model is not model or call_e.model is not model or (entry_e.start, entry_e.end) != (call_e.start, call_e.end):
        raise Unsupported('P4 readonly event differs from the exact selected UI entry quote')
    target = normalized_callee(entry.get('callee')); helper_op['helper_call']['literal_method'] = target[-1] if target else ''
    helper = engine.p2_escaped_helper(helper_op, fn.parent, call_e.start)
    if helper['owner'] + (helper['method'],) != target:
        raise Unsupported('P4 readonly UI helper owner differs from the selected entry')
    hits = []
    for ts in model.lists:
        for token in ts:
            if token.kind != 'template' or not call_e.contains(token):
                continue
            markup, exprs = template_markup(token); hp = TableHTMLContext(exprs); hp.feed(markup); hp.close()
            for event, handler in hp.handlers:
                if event == 'click' and exact_handler(handler, target, exprs, helper):
                    tokens = exprs.get(handler.strip(), []); cs = calls(tokens)
                    if len(cs) != 1 or len(cs[0].args) != len(params) + 1 or [t.value for t in cs[0].args[ri+1]] != ['true']:
                        raise Unsupported('P4 selected UI call does not pass the original readonly slot literally true')
                    hits.append(token)
    if len(hits) != 1:
        raise Unsupported('P4 readonly role needs one exact original active selected event')
    # Capture readonly into the dialog consumed by the original save guard.
    dialog_e = engine.evidence(op.get('dialog_binding_evidence'))
    if dialog_e.model is not model:
        raise Unsupported('P4 readonly dialog capture is disconnected')
    capture_token, capture_rhs = _p4_anchor_binding(model, fn, op['dialog_capture_symbol'], dialog_e)
    if not capture_rhs or capture_rhs[0].value != '{' or matching(capture_rhs).get(0) != len(capture_rhs)-1:
        raise Unsupported('P4 readonly dialog capture is not one literal object')
    props = {}
    for part in split_tokens(capture_rhs[1:-1]):
        if not part:
            continue
        key = part[0].value if part[0].kind == 'id' else literal(part[0])
        if key is None or key in props:
            raise Unsupported('P4 readonly dialog has a computed/duplicate property')
        if len(part) == 1 and key == 'readonly' and part[0].value == readonly:
            props[key] = part
        elif len(part) >= 3 and part[1].value == ':':
            props[key] = part[2:]
        else:
            raise Unsupported('P4 readonly dialog has an unsupported spread/shorthand')
    if [t.value for t in props.get('readonly', [])] != [readonly]:
        raise Unsupported('P4 dialog does not preserve the computed original readonly value')
    assignments = []
    for ts in model.lists:
        for i, token in enumerate(ts[:-3]):
            if dialog_e.start <= token.start < dialog_e.end and [t.value for t in ts[i:i+4]] == [op['dialog_symbol'], '=', op['dialog_capture_symbol'], ';'] and model.enclosing_function(token.start) is fn:
                assignments.append((token, ts[i+2]))
    if len(assignments) != 1:
        raise Unsupported('P4 original dialog is not assigned its exact readonly capture')
    # The original layer may restore exactly this same readonly capture.
    # A callback cannot mutate it after the layer call starts. Only the real
    # onRestore(){dialog=current;} copy is a separately verified initializer.
    restores = []
    for child in model.functions:
        if child.parent is not fn or not layer.start < child.start < child.end <= layer.end:
            continue
        prefix = model.source[child.start:child.body_start]
        if not re.fullmatch(r'onRestore\s*\(\s*\)\s*', prefix):
            continue
        child_bodies = []
        for ts in model.lists:
            ms = matching(ts)
            for i, token in enumerate(ts):
                if token.start == child.body_start and token.value == '{':
                    child_bodies.append(ts[i+1:ms[i]])
        if len(child_bodies) != 1:
            raise Unsupported('P4 readonly restore callback lacks one exact body')
        restore = child_bodies[0]
        if [t.value for t in restore] == [op['dialog_symbol'], '=', op['dialog_capture_symbol'], ';']:
            restores.append((restore[0], restore[2]))
    allowed_writes = [readonly_token, result_token, capture_token, assignments[0][0]] + [a for a,b in restores]
    allowed_copies = [assignments[0][1]] + [b for a,b in restores]
    _p4_anchor_no_writes(engine, model, {ident, op['readonly_parameter'], readonly, result, op['dialog_capture_symbol'], op['dialog_symbol']}, fn.body_start, fn.body_end, allowed_writes)
    engine.p2_protected_escapes(model, {op['dialog_capture_symbol'], op['dialog_symbol']}, capture_rhs[-1].end, fn.body_end, allowed_copies, label='P4 readonly dialog capture')
    guard_e = engine.evidence(op.get('save_guard_evidence'))
    if guard_e.model is not model:
        raise Unsupported('P4 readonly save guard is disconnected')
    save_fn, _, save_body = _p4_anchor_function(model, op['save_symbol'])
    save_binding = op.get('save_owner_binding')
    if not isinstance(save_binding, dict) or save_binding.get('kind') != 'static_owner_export' or normalized_callee(save_binding.get('callee')) != save_callee or save_binding.get('symbol') != op['save_symbol']:
        raise Unsupported('P4 readonly guard must bind the exact save control owner/export')
    bound_save = engine.explicit_owner_export(engine.evidence(save_binding.get('evidence')), save_binding)
    if bound_save[0] is not save_fn or bound_save[1] is not fn.parent:
        raise Unsupported('P4 readonly save control resolves to another implementation/scope')
    cap = op['dialog_capture_symbol']; factory = op['access_factory_symbol']; method = op['access_method']
    expected_guard = ['const', cap, '=', op['dialog_symbol'], ';', 'if', '(', '!', cap, '||', cap, '.', 'busy', '||', cap, '.', 'readonly', '||', cap, '.', 'key', '!==', factory, '(', ')', '.', 'context', '(', ')', '||', '!', factory, '(', ')', '.', method, '(', 'true', ')', ')', 'return', ';']
    if [t.value for t in save_body[:len(expected_guard)]] != expected_guard or not guard_e.contains(save_body[0]) or not guard_e.contains(save_body[len(expected_guard)-1]):
        raise Unsupported('P4 original save does not immediately reject its captured readonly dialog')
    return []


def original_anchor_role_errors(review, proof, record, scope_review, candidate_path, anchor_context):
    """Finite P4 opt-in only; no metadata-only role or general JS inference."""
    try:
        contract = proof.get('original_anchor')
        if not isinstance(contract, dict) or contract.get('version') != 'bounded-original-anchor-role@1':
            raise Unsupported('P4 original anchor contract is missing')
        if contract.get('role') == 'initiator_only_needs_save_result' and anchor_context.get('role') == contract['role']:
            op=contract.get('role_evidence')
            if not isinstance(op,dict)or op.get('version')!='bounded-active-followup-role@1'or op.get('reachability')!='conditional_only':raise Unsupported('Original initiator needs an explicit actual user-save source-flow contract')
            engine=ChainReview(review,proof,record,scope_review,candidate_path)
            engine.validate_active_flow(require_followup=True)
            return []
        if contract.get('role') != 'result_expression_noncallable' or anchor_context.get('role') != contract['role']:
            raise Unsupported('P4 original anchor role lexical support is not implemented; follow-up save/result remains pending')
        engine = ChainReview(review, proof, record, scope_review, candidate_path)
        return _p4_readonly_result_role(engine, contract, anchor_context)
    except (Unsupported, TypeError, KeyError, ValueError, AttributeError, RecursionError, OSError, IndexError) as exc:
        return ['Bounded original-anchor role contract pending: ' + str(exc)]
