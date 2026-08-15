# Patches do estado final

Patches reais (formato `git format-patch`, aplicáveis com `git am`) que levam o
tip bagunçado da sessão ao estado final desejado.

## Base e alvo

| | commit | descrição |
|---|---|---|
| **base** | `e2260bd` | tip da sessão `01a0011b`, com o revert aplicado e o CI quebrado |
| **alvo** | `9177c53` | auditoria restaurada, árvore idêntica ao `main`, CI verde |

## Arquivos

| Patch | Efeito |
|---|---|
| `0001-Restore-Windows-WSL-skill-audit-lost-to-accidental-r.patch` | Restaura os 6 arquivos da auditoria (+964 / −294). Faz a árvore ficar idêntica ao `origin/main`. |
| `0002-Document-recovery-of-session-01a0011b.patch` | Adiciona `RECUPERACAO-SESSAO.md` com a linha do tempo e o diagnóstico. |

## Como aplicar

```bash
git checkout e2260bd            # ou a branch que estiver nesse estado
git am /caminho/para/patches/*.patch
```

Alternativa sem criar commits (só a árvore):

```bash
git apply patches/*.patch
```

## Verificação feita

Aplicados em um clone limpo a partir de `e2260bd`:

```
git am patches/*.patch                      -> Applying: ... (2/2, sem conflito)
git diff 9177c53 --stat                     -> vazio (árvore idêntica ao alvo)
python -m py_compile scripts/audits/...     -> ok
python scripts/audits/audit_windows_wsl_skill.py -> 0 errors, 0 warnings
python -m pytest tests/test_windows_wsl_skill.py -q -> 4 passed
```

Os três arquivos que o workflow `.github/workflows/windows-wsl-skill-audit.yml`
invoca passam a existir, que era exatamente a causa da falha do CI.
