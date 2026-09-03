"""Multi-language parsing: TS/JS/React, Go, Java, Rust + end-to-end indexing."""

from __future__ import annotations

import pytest

from coderag.parsing.registry import get_parser_for_path, module_qualified_name

TSX = """
import React, { useState } from "react";
import { fetchUser } from "./api";

export const UserCard = ({ id }: { id: string }) => {
  const [user, setUser] = useState(null);
  fetchUser(id).then(setUser);
  return <div className="card">{user?.name}</div>;
};

export default function App() {
  return <UserCard id="1" />;
}

export class ErrorBoundary extends React.Component {
  componentDidCatch(err: Error) { console.error(err); }
  render() { return this.props.children; }
}

export interface UserProps { id: string }
"""

GO = """
package main

import (
\t"fmt"
\t"net/http"
)

type Server struct{ port int }

func NewServer(port int) *Server { return &Server{port: port} }

func (s *Server) Start() error {
\tfmt.Println("starting")
\treturn http.ListenAndServe(":8080", nil)
}
"""

JAVA = """
package com.example;

import java.util.List;

public class PaymentService {
    public PaymentService(List<String> log) {}
    public void retry(String id) { process(id); }
    private void process(String id) {}
}
"""

RUST = """
use std::collections::HashMap;

pub struct Cache { map: HashMap<String, String> }

pub trait Store { fn get(&self, k: &str) -> Option<String>; }

impl Cache {
    pub fn new() -> Self { Cache { map: HashMap::new() } }
}

pub fn helper() -> i32 { compute(2) }
"""


def _parse(path: str, src: str):
    parser = get_parser_for_path(path)
    assert parser is not None, f"no parser for {path}"
    return parser.parse(module_qualified_name(path), src)


def _by_name(result):
    return {s.qualified_name: s for s in result.symbols}


def test_react_tsx_components_and_classes():
    syms = _by_name(_parse("src/components/App.tsx", TSX))
    # the arrow-function component IS a symbol — the React essential
    card = syms["src.components.App.UserCard"]
    assert card.symbol_type == "function"
    assert "UserCard" in card.source_code and "useState" in card.source_code
    assert syms["src.components.App.App"].symbol_type == "function"
    assert syms["src.components.App.ErrorBoundary"].symbol_type == "class"
    assert syms["src.components.App.ErrorBoundary.render"].symbol_type == "method"
    assert syms["src.components.App.UserProps"].symbol_type == "class"


def test_tsx_imports_and_calls():
    result = _parse("App.tsx", TSX)
    rels = {(r.relationship_type, r.target_name) for r in result.relationships}
    assert ("IMPORTS", "react") in rels
    assert ("IMPORTS", "./api") in rels
    assert ("CALLS", "fetchUser") in rels


def test_go_structs_functions_methods():
    result = _parse("cmd/server/main.go", GO)
    syms = _by_name(result)
    assert syms["cmd.server.main.Server"].symbol_type == "class"
    assert syms["cmd.server.main.NewServer"].symbol_type == "function"
    assert syms["cmd.server.main.Start"].symbol_type == "method"
    rels = {(r.relationship_type, r.target_name) for r in result.relationships}
    assert ("IMPORTS", "net/http") in rels
    assert ("CALLS", "ListenAndServe") in rels


def test_java_classes_and_methods():
    result = _parse("src/main/java/PaymentService.java", JAVA)
    syms = _by_name(result)
    q = "src.main.java.PaymentService"
    assert syms[f"{q}.PaymentService"].symbol_type == "class"
    # constructor is a method nested under the class
    assert syms[f"{q}.PaymentService.PaymentService"].symbol_type == "method"
    assert syms[f"{q}.PaymentService.retry"].symbol_type == "method"
    rels = {(r.relationship_type, r.target_name) for r in result.relationships}
    assert ("IMPORTS", "java.util.List") in rels
    assert ("CALLS", "process") in rels


def test_rust_structs_traits_impls():
    result = _parse("src/lib.rs", RUST)
    syms = _by_name(result)
    assert syms["src.lib.Cache"].symbol_type == "class"
    assert syms["src.lib.Store"].symbol_type == "class"
    assert syms["src.lib.Store.get"].symbol_type == "method"    # trait signature
    assert syms["src.lib.Cache.new"].symbol_type == "method"    # impl method
    assert syms["src.lib.helper"].symbol_type == "function"
    rels = {(r.relationship_type, r.target_name) for r in result.relationships}
    assert ("IMPORTS", "std::collections::HashMap") in rels
    assert ("CALLS", "compute") in rels


def test_symbols_are_searchable():
    result = _parse("App.tsx", TSX)
    card = next(s for s in result.symbols if s.symbol_name == "UserCard")
    assert "user" in card.search_terms and "card" in card.search_terms


def test_javascript_plain_and_jsx_extensions():
    js = "export const add = (a, b) => a + b;\nfunction sub(a, b) { return a - b; }\n"
    for path in ("util.js", "util.jsx", "util.mjs"):
        syms = {s.symbol_name for s in _parse(path, js).symbols}
        assert {"add", "sub"} <= syms


# ---- end-to-end: index a mixed-language repo -------------------------------

@pytest.mark.db
def test_index_and_search_mixed_repo(engine, db_session, tmp_path):
    import subprocess

    from coderag.service import run_index, run_search

    repo = tmp_path / "mixedrepo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "App.tsx").write_text(TSX)
    (repo / "src" / "main.go").write_text(GO)
    (repo / "src" / "lib.rs").write_text(RUST)
    (repo / "src" / "Service.java").write_text(JAVA)
    (repo / "src" / "helper.py").write_text("def area(r):\n    return 3.14 * r * r\n")
    # node_modules must be ignored even when huge
    nm = repo / "node_modules" / "react"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("module.exports = {};\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=repo, check=True)

    _repo, stats = run_index(db_session, str(repo), "mixedrepo")
    assert stats.files_indexed == 5                      # node_modules skipped

    _r, outcome = run_search(db_session, "UserCard component", "mixedrepo",
                             top_n=5, record=False)
    names = [c.qualified_name for c in outcome.candidates]
    assert any("UserCard" in n for n in names)

    _r, outcome = run_search(db_session, "NewServer", "mixedrepo",
                             top_n=5, record=False)
    assert any("NewServer" in c.qualified_name for c in outcome.candidates)
