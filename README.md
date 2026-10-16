# Monitor de apuração — 2º turno presidencial 2026

Lê os resultados do TSE por UF a cada 60 s e compara com os 2º turnos de 2014, 2018 e 2022.

## Rodar
1. Duplo clique em `iniciar.bat` (instala as dependências na 1ª vez e abre o painel no navegador).
2. Na barra lateral, escolha a fonte:
   - **2º turno (TSE, automático)**: detecta sozinho o código do 2º turno na lista oficial do TSE. Usar em 25/10 a partir das 17h.
   - **Teste: 1º turno 2026 (TSE)**: lê o 1º turno (código 6257) enquanto os arquivos estiverem no ar. Serve para validar a conexão com o TSE na sua máquina.
   - **Simulação local**: use `simular.bat`, que sobe um servidor local imitando o TSE e apura em 15 min.

## Arquivos
| Arquivo | Função |
|---|---|
| `coletor.py` | Baixa os `.jws` do TSE (com ETag), decodifica e grava em SQLite (`apuracao*.db`). Roda sozinho: `python coletor.py --uma-vez --eleicao 6257` |
| `analise.py` | Δ p.p. por UF, parcial bruta, projeção ajustada, série da noite |
| `app.py` | Painel Streamlit (o coletor roda numa thread dentro do app) |
| `template.html` | Layout do painel (o do protótipo aprovado) |
| `simulador_tse.py` | Servidor local que imita o TSE para testes |
| `base_historica_2t_2014_2018_2022.csv` | Votos por UF dos 2º turnos (TSE, Dados Abertos) |
| `primeiro_turno_2026_uf.csv` | 1º turno 2026 por UF, usado como peso na projeção |

## Método
- % de Lula e Flávio calculados sobre os votos dos dois (no 2º turno, igual aos votos válidos).
- Projeção ajustada = Σ (% parcial da UF × válidos da UF no 1º turno 2026) / Σ válidos. UFs com menos de 0,5% apurado usam 2022 + swing médio das UFs já apuradas.
- Abstenção e brancos+nulos: parcial bruta e projeção ajustada (peso = eleitorado de cada UF; UF sem dados = 2022 + variação média das já apuradas).
- Decomposição do saldo do PT (votos PT − adversário) vs ano-base em 4 efeitos que somam o total: eleitorado, comparecimento, brancos e nulos, preferência.
- Limite do TSE: 100 req/s por IP. O coletor faz ~20 req/s, 29 arquivos por minuto.

## Erro de conexão
O painel mostra "Atenção" com o erro. Abra no navegador
https://resultados.tse.jus.br/oficial/comum/config/ele-c.json — se abre no navegador e falha no Python, o bloqueio é proxy/antivírus da rede.
