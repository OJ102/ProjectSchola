#!/bin/bash
# Turn a markdown reply into plain speakable text. stdin -> stdout. No shell evaluation of the text.
# Multi-line constructs are removed on the whole text; everything else runs line by line, which keeps
# jq's gsub cost per string small. Math (LaTeX and plain operators) is turned into words, not deleted.
# Callers should cap the input size (speak.sh does) — gsub cost grows with matches per line.
exec jq -Rrs '
# Apply f until the value stops changing, at most n times.
def fixpoint(f; n): if n <= 0 then . else (f) as $x | if $x == . then . else $x | fixpoint(f; n - 1) end end;

def latex_words:
  gsub("\\\\(?:left|right|displaystyle|quad|qquad)\\b|\\\\[,;:!]"; " ")
  # structural rules match innermost braces only, so repeat until nested \\frac/\\sqrt/^{} are all resolved
  | fixpoint(
      gsub("\\\\[dt]?frac\\s*\\{(?<a>[^{}]*)\\}\\s*\\{(?<b>[^{}]*)\\}";
           (.a | gsub("^\\s+|\\s+$"; "")) as $a | (.b | gsub("^\\s+|\\s+$"; "")) as $b
           | if ($a | test("[\\s+=-]")) then " \($a), over \($b) " else " \($a) over \($b) " end)
      | gsub("\\\\sqrt\\s*\\{(?<a>[^{}]*)\\}"; " the square root of \(.a) ")
      | gsub("\\^\\s*(?:\\{\\s*2\\s*\\}|2(?!\\d))"; " squared ")
      | gsub("\\^\\s*(?:\\{\\s*3\\s*\\}|3(?!\\d))"; " cubed ")
      | gsub("\\^\\s*\\{(?<e>[^{}]*)\\}"; " to the power of \(.e) ")
      | gsub("_\\s*\\{(?<e>[^{}]*)\\}"; " sub \(.e) ")
      | gsub("\\\\(?:left|right)\\b|\\\\[,;:!]"; " ")
      | gsub("\\\\(?:text|mathrm|mathbf|mathit|mathsf|operatorname|boldsymbol)\\s*\\{(?<a>[^{}]*)\\}"; " \(.a) "); 6)
  | gsub("\\^\\s*(?<e>\\\\?\\w)"; " to the power of \(.e) ")
  | gsub("_\\s*(?<e>\\w)"; " sub \(.e) ")
  | gsub("\\\\(?:cdot|times)\\b|\\*"; " times ")
  | gsub("\\\\div\\b"; " divided by ")
  | gsub("\\\\pm\\b"; " plus or minus ")
  | gsub("\\\\(?:leq?|leqslant)\\b"; " less than or equal to ")
  | gsub("\\\\(?:geq?|geqslant)\\b"; " greater than or equal to ")
  | gsub("\\\\neq?\\b"; " not equal to ")
  | gsub("\\\\approx\\b"; " approximately ")
  | gsub("\\\\(?:to|rightarrow|implies)\\b"; " to ")
  | gsub("\\\\infty\\b"; " infinity ")
  | gsub("\\\\sum\\b"; " the sum of ")
  | gsub("\\\\int\\b"; " the integral of ")
  | gsub("\\\\(?<w>[A-Za-z]+)"; " \(.w) ")
  | gsub("<="; " less than or equal to ") | gsub(">="; " greater than or equal to ")
  | gsub("="; " equals ") | gsub("\\+"; " plus ") | gsub("-"; " minus ")
  | gsub("/"; " over ") | gsub("<"; " less than ") | gsub(">"; " greater than ")
  | gsub("[{}\\\\&]"; " ");

def html_tag: "</?(?:a|abbr|b|big|blockquote|br|center|cite|code|del|details|div|em|figure|figcaption|font|h[1-6]|hr|i|img|ins|kbd|li|mark|ol|p|pre|q|s|samp|small|span|strike|strong|sub|summary|sup|table|tbody|td|tfoot|th|thead|tr|tt|u|ul|var)\\b[^<>\\n]*>";

def line_rules:
  # block markers: callouts, quotes, headings, rules, list bullets
  gsub("^[ \\t]*(?:>[ \\t]?)+"; "")
  | gsub("\\[![A-Za-z-]+\\][+-]?"; " ")
  | gsub("^[ \\t]*#{1,6}[ \\t]+"; "")
  | gsub("^[ \\t]*(?:[-*_][ \\t]*){3,}$"; "")
  | gsub("^[ \\t]*(?:[-*+]|\\d+[.)])[ \\t]+(?:\\[[ xX]\\][ \\t]+)?"; "")
  # inline code first, so markup inside it never turns into speech
  | gsub("`+[^`]*`+"; " ")
  | gsub(html_tag; " "; "i")
  # inline LaTeX: \(..\) and $..$ (pandoc rules so "$5 and $10" survives) become words
  | gsub("\\\\\\((?<m>.*?)\\\\\\)"; " \(.m | latex_words) ")
  | gsub("(?<![\\\\$])\\$(?<m>[^\\s$](?:[^$]*?[^\\s\\\\$])?)\\$(?!\\d)"; " \(.m | latex_words) ")
  # Obsidian embeds and images go; wikilinks and links keep their label
  | gsub("!\\[\\[[^\\]]*\\]\\]"; " ")
  | gsub("!\\[[^\\]]*\\]\\([^)]*\\)"; " ")
  | gsub("\\[\\[(?:[^\\]|]*\\|)?(?<t>[^\\]]*)\\]\\]"; "\(.t)")
  | gsub("\\[(?<t>[^\\]]*)\\]\\([^)]*\\)"; "\(.t)")
  # URLs
  | gsub("\\b(?:https?|ftp|file)://[^\\s)>\\]]+"; " "; "i")
  | gsub("\\bwww\\.[^\\s)>\\]]+"; " "; "i")
  # plain-text math before emphasis, so "3 * 4 * 5" is not read as emphasis
  | gsub("(?<=[\\w)]) \\*\\* (?=[\\w(])|(?<=[\\w)])\\*\\*(?=[\\d(])"; " to the power of ")
  | gsub("(?<=[\\w)])\\^\\s?2(?![\\d.])"; " squared ")
  | gsub("(?<=[\\w)])\\^\\s?3(?![\\d.])"; " cubed ")
  | gsub("(?<=[\\w)])\\^\\s?(?<e>-?[\\w.]+)"; " to the power of \(.e)")
  | gsub("(?<=\\w)\\*(?=\\w)"; " times ")
  | gsub("(?<=\\S) \\* (?=\\S)|(?<=[\\d)])\\*(?=[\\d(])|\\s[×✕]\\s?"; " times ")
  | gsub("<=|≤"; " less than or equal to ") | gsub(">=|≥"; " greater than or equal to ")
  | gsub("!=|≠"; " not equal to ") | gsub("≈"; " approximately ")
  | gsub("->|=>|→|⇒"; " to ") | gsub("<-|←"; " from ") | gsub("÷"; " divided by ")
  | gsub("<"; " less than ") | gsub(">"; " greater than ")
  | gsub("(?<![\\w#])(?<l>[A-Za-z])#(?![\\w#])"; "\(.l) sharp")
  # emphasis
  | gsub("(?<d>\\*\\*|__)(?<t>.+?)\\k<d>"; "\(.t)")
  | gsub("(?<![\\w*])\\*(?<t>[^*]+?)\\*(?![\\w*])"; "\(.t)")
  | gsub("(?<![\\w_])_(?<t>[^_]+?)_(?![\\w_])"; "\(.t)")
  | gsub("~~(?<t>.+?)~~"; "\(.t)")
  | gsub("&nbsp;"; " ") | gsub("&amp;"; " and ") | gsub("&lt;"; " less than ") | gsub("&gt;"; " greater than ");

def is_table_sep: test("^\\s*\\|?\\s*:?-{3,}:?\\s*(\\|\\s*:?-{3,}:?\\s*)*\\|?\\s*$") and test("\\|");

# Drop pipe tables: lines starting with "|", plus any run of "|" lines around a separator row.
def drop_tables:
  . as $l
  | [range(0; $l | length) | select($l[.] | is_table_sep)] as $seps
  | reduce $seps[] as $s ({};
      ($s | until(. <= 0 or ($l[. - 1] | test("\\|") | not); . - 1)) as $a
      | ($s | until(. >= ($l | length) - 1 or ($l[. + 1] | test("\\|") | not); . + 1)) as $b
      | reduce range($a; $b + 1) as $i (.; .[$i | tostring] = true))
  | . as $drop
  | [range(0; $l | length) | select($drop[tostring] | not) | $l[.] | select(test("^[ \\t]*\\|") | not)];

# jq gsub cost grows with the square of matches per string, so long lines are cut into ~300-char pieces
# at spaces (an inline construct straddling a cut just loses its markup).
def chunks:
  if length <= 400 then [.]
  else reduce (split(" ")[]) as $w ([""];
         if (.[-1] | length) + ($w | length) > 300 and (.[-1] | length) > 0 then . + [$w]
         else .[-1] += (if .[-1] == "" then $w else " " + $w end) end)
  end;

# Markup characters, pictographs and modifiers go, and every bracket too, so `say` never sees an
# embedded [[command]]. Whitespace is collapsed with split/join (linear) rather than gsub.
def leftovers:
  gsub("[`*#|~\\\\\\[\\]{}^]"; " ")
  | gsub("[\\p{So}\\p{Sk}\\p{Me}\\p{Co}\\x{FE0F}\\x{FE0E}\\x{200D}\\x{FFFD}\\x{1F3FB}-\\x{1F3FF}]"; " ")
  | gsub("\\t"; " ") | split(" ") | map(select(. != "")) | join(" ")
  | gsub(" (?<p>[.,;:!?])"; "\(.p)");

# Whole-text passes: fenced blocks (any fence length; unclosed runs to the end), svg, comments, display math
gsub("\\r"; "")
| gsub("(?m)^[ \\t]*(?<f>`{3,}|~{3,})[^\\n]*\\n[\\s\\S]*?(?:^[ \\t]*\\k<f>[`~]*[ \\t]*$|\\z)"; "\n")
| gsub("<svg\\b[\\s\\S]*?(?:</svg>|\\z)"; " "; "i")
| gsub("<!--[\\s\\S]*?(?:-->|\\z)"; " ")
| gsub("\\$\\$(?<m>[\\s\\S]*?)\\$\\$"; " \(.m | latex_words) ")
| gsub("\\\\\\[(?<m>[\\s\\S]*?)\\\\\\]"; " \(.m | latex_words) ")
| gsub("\\$\\$|\\\\\\[|\\\\\\]"; " ")
| split("\n") | drop_tables
| map(chunks | map(if test("[^A-Za-z0-9 ,.?!;:\u0027\"()]") then line_rules else . end | leftovers) | join(" ") | ltrimstr(" ") | rtrimstr(" ")
      | select(test("^[.,;:]+$") | not))
| join("\n")
| gsub("\n{3,}"; "\n\n")
| sub("^\\s+"; "") | sub("\\s+$"; "")
'
