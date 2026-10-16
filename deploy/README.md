# Monitor de apuração — 2º turno presidencial 2026

Painel Streamlit que lê os resultados do TSE por UF (arquivos `.jws`, a cada 60 s) e compara com o
1º turno de 2026 e com os 2º turnos de 2014, 2018 e 2022.

## Arquivos
| Arquivo | Função |
|---|---|
| `app.py` | Painel (Streamlit). O coletor roda em segundo plano dentro do app |
| `coletor.py` | Baixa e decodifica os `.jws` do TSE e grava em SQLite (só o que mudou) |
| `analise.py` | Projeção ajustada, abstenção, decomposição do saldo, série da noite |
| `template.html` | Layout do painel |
| `base_historica_2t_2014_2018_2022.csv` | 2º turnos por UF (TSE, Dados Abertos) |
| `primeiro_turno_2026_uf.csv` | 1º turno 2026 por UF (TSE) |
| `requirements.txt` | Dependências |

## Configuração opcional (Streamlit Cloud → Settings → Secrets)
```toml
MOSTRAR_TESTE = "0"   # esconde o modo "Teste: 1º turno" (usar depois que o 2º turno começar)
```

## Limitações da versão online
- O disco do Streamlit Cloud não é permanente: se o app reiniciar ou dormir (12 h sem acesso),
  a curva "evolução da noite" recomeça. Os números atuais são relidos do TSE na hora.
- Abra o app algumas horas antes das 17h do dia 25/10 para garantir que está acordado.

Fontes: TSE — resultados.tse.jus.br e dadosabertos.tse.jus.br.
