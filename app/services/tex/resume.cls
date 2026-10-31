% Roleveya's resume class: a stand-in for the resume.cls file that comes with the widely used
% "Medium Length Professional CV" template, written from scratch with the same commands so CVs made
% from that template compile here without the original file.
%
%   \name{...}                 your name, printed large and centred at the top
%   \address{...}              up to three centred lines under the name; \\ separates items in a line
%   rSection{title}            a section with a ruled heading
%   rSubsection{a}{b}{c}{d}    an entry: a (bold) with b on the right, then c (italic) with d on the
%                              right (left out when c is empty), then \item bullets
%
% \namesize, \nameskip, \addressskip, \sectionskip, \sectionlineskip and \addressSep can be
% redefined in the document, as with the original.
\NeedsTeXFormat{LaTeX2e}
\ProvidesClass{resume}[2026/10/31 Roleveya resume class]

% The original's defaults (11pt, US letter), unless the document asks for another size or paper.
\def\rv@size{11pt}
\def\rv@paper{letterpaper}
\DeclareOption{10pt}{\def\rv@size{10pt}}
\DeclareOption{11pt}{\def\rv@size{11pt}}
\DeclareOption{12pt}{\def\rv@size{12pt}}
\DeclareOption{a4paper}{\def\rv@paper{a4paper}}
\DeclareOption{letterpaper}{\def\rv@paper{letterpaper}}
\DeclareOption*{\PassOptionsToClass{\CurrentOption}{article}}
\ProcessOptions\relax
\edef\rv@options{\rv@size,\rv@paper}
\expandafter\LoadClass\expandafter[\rv@options]{article}

% Templates made for the original rely on these (array for >{\bfseries}l columns, ifthen for \ifthenelse).
\RequirePackage[parfill]{parskip}
\RequirePackage{array}
\RequirePackage{ifthen}
\pagestyle{empty}

\newcommand\namesize{\huge}
\newcommand\nameskip{\bigskip}
\newcommand\addressskip{\smallskip}
\newcommand\sectionskip{\medskip}
\newcommand\sectionlineskip{\medskip}
\newcommand\addressSep{$\diamond$}

\def\rv@name{}
\def\rv@addresses{}
\newcommand\name[1]{\def\rv@name{#1}}
\newcommand\address[1]{\g@addto@macro\rv@addresses{\printaddress{#1}}}

\newcommand\printname{%
  {\centering\namesize\bfseries\MakeUppercase{\rv@name}\par}%
  \nameskip}

\newcommand\printaddress[1]{%
  {\centering
   \def\\{\unskip\ \addressSep\ \ignorespaces}%
   #1\par}%
  \addressskip}

% After every package's begin-document code (hyperref's included), so links in the heading work.
\AddToHook{begindocument/end}{%
  \ifx\rv@name\@empty\else\printname\fi
  \rv@addresses}

\newenvironment{rSection}[1]{%
  \sectionskip
  {\bfseries\MakeUppercase{#1}\par}%
  \sectionlineskip
  \hrule
  \begin{list}{}{\setlength\leftmargin{1.5em}}%
  \item[]%
}{%
  \end{list}}

\newenvironment{rSubsection}[4]{%
  {\bfseries #1}\hfill #2%
  \if\relax\detokenize{#3}\relax\else
    \\{\itshape #3}\hfill{\itshape #4}%
  \fi
  \smallskip
  \begin{list}{$\cdot$}{\setlength\leftmargin{0em}}%
  \setlength\itemsep{-0.5em}%
  \vspace{-0.5em}%
}{%
  \end{list}%
  \vspace{0.5em}}
