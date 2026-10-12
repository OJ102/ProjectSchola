#!/bin/bash
# Turn a markdown reply into plain speakable text. stdin -> stdout. No shell evaluation of the text.
exec jq -Rrs '
  # fenced code blocks (incl. mermaid/svg); an unclosed fence runs to the end
  gsub("(?m)^[ \\t]*(```|~~~)[\\s\\S]*?(^[ \\t]*\\1[^\\n]*$|\\z)"; "\n")
  | gsub("<svg[\\s\\S]*?(</svg>|\\z)"; " "; "i")
  | gsub("<!--[\\s\\S]*?(-->|\\z)"; " ")
  | gsub("<[A-Za-z/][^>\\n]*>"; " ")
  # LaTeX: display, then \[..\], \(..\), then inline $..$ (pandoc rules so "$5 and $10" survives)
  | gsub("\\$\\$[\\s\\S]*?(\\$\\$|\\z)"; " ")
  | gsub("\\\\\\[[\\s\\S]*?(\\\\\\]|\\z)"; " ")
  | gsub("\\\\\\([\\s\\S]*?(\\\\\\)|\\z)"; " ")
  | gsub("(?<![\\\\$])\\$(?=[^\\s$])[^$\\n]*?(?<=[^\\s\\\\$])\\$(?!\\d)"; " ")
  # Obsidian embeds and images go; wikilinks and links keep their label
  | gsub("!\\[\\[[^\\]\\n]*\\]\\]"; " ")
  | gsub("!\\[[^\\]\\n]*\\]\\([^)\\n]*\\)"; " ")
  | gsub("\\[\\[(?:[^\\]|\\n]*\\|)?(?<t>[^\\]\\n]*)\\]\\]"; "\(.t)")
  | gsub("\\[(?<t>[^\\]\\n]*)\\]\\([^)\\n]*\\)"; "\(.t)")
  # inline code, URLs
  | gsub("`+[^`\\n]*`+"; " ")
  | gsub("\\b(?:https?|ftp|file)://[^\\s)>\\]]+"; " "; "i")
  | gsub("\\bwww\\.[^\\s)>\\]]+"; " "; "i")
  # tables: any line that starts with a pipe
  | gsub("(?m)^[ \\t]*\\|.*$"; "")
  # block markers: callouts, quotes, headings, rules, list bullets
  | gsub("(?m)^[ \\t]*(>[ \\t]*)+"; "")
  | gsub("\\[![A-Za-z-]+\\][+-]?"; " ")
  | gsub("(?m)^[ \\t]*#{1,6}[ \\t]+"; "")
  | gsub("(?m)^[ \\t]*([-*_][ \\t]*){3,}$"; "")
  | gsub("(?m)^[ \\t]*(?:[-*+]|\\d+[.)])[ \\t]+(?:\\[[ xX]\\][ \\t]+)?"; "")
  # emphasis
  | gsub("(\\*\\*|__)(?<t>[^\\n]+?)\\1"; "\(.t)")
  | gsub("(?<![\\w*])\\*(?<t>[^*\\n]+?)\\*(?![\\w*])"; "\(.t)")
  | gsub("(?<![\\w_])_(?<t>[^_\\n]+?)_(?![\\w_])"; "\(.t)")
  | gsub("~~(?<t>[^\\n]+?)~~"; "\(.t)")
  | gsub("&nbsp;"; " ") | gsub("&amp;"; "and") | gsub("&lt;"; "<") | gsub("&gt;"; ">")
  # leftover markup characters and pictographic symbols
  | gsub("[`*#|~\\\\]"; " ")
  | gsub("[\\p{So}\\p{Cs}\\x{FE0F}\\x{200D}]"; " ")
  | gsub("[ \\t]+"; " ")
  | gsub("(?m)^ +| +$"; "")
  | gsub("\\n{3,}"; "\n\n")
  | sub("^\\s+"; "") | sub("\\s+$"; "")
'
