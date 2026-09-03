# Livecaster, živý kopilot pre podcastera

**Čo to je:** screencast o nástroji, ktorý prepisuje túto nahrávku.
**Formát:** sám, jeden mikrofón, asi 15 minút.
**Pravidlo pre seba:** najprv to ukázať bežať, až potom vysvetľovať, ako to funguje.

---

## 1. Problém

- Nahrávam dlhé rozhovory a ku každému si píšem osnovu
- V polovici rozhovoru stratím prehľad, čo sme už prebrali, a zistím to o týždne neskôr pri strihu
- Pokazí sa to dvoma spôsobmi: pýtam sa na to, čo už odznelo, alebo zabudnem na jedinú otázku, na ktorej mi záležalo
- Papierové poznámky sa samy neaktualizujú. Druhá obrazovka plná textu je horšia než nič

**Otázka, ktorú treba na kameru zodpovedať:** prečo to nie je len „pusti Whisper po nahrávaní“?

## 2. Čo to robí

- Otvoriť mapu. Osnova je celé rozhranie
- Rozprávať o téme, počkať, sledovať, ako sa riadok preškrtne aj s časom
- Tri stavy: ~~prebrané~~, ◐ dotknuté, ⏭ preskočené
	- na „prebrané“ treba doslovný citát z prepisu, inak sa to neráta
	- letmá zmienka dostane len „dotknuté“
- Now a Next: jeden riadok a potom tri návestia po piatich slovách
	- klávesa `d` ukáže zdôvodnenie modelu a premostenie, ktoré navrhuje
	- počas rozprávania nikdy nečítaš odstavec
- Stlačiť `c` na tému, ktorú model prehliadol. Potom má do nej na desať minút zákaz vstupu
- Mentions: knihy, ľudia a odkazy vytiahnuté z rozhovoru priebežne, ako zaznejú
- Stlačiť Finish a otvoriť záložku Result

## 3. Čo z toho vypadne na konci

Show notes so zhrnutím, kapitolami, návrhmi titulkov, popismi a jedným príspevkom na sociálne
siete. Pôvodná osnova, znak po znaku, doplnená o to, čo sa prebralo a kedy. Prepis ako Markdown
a ako SRT. Surové JSON, aby sa poznámky dali pregenerovať bez ďalšieho platenia modelu.

Ukázať synchronizačnú značku. Stlačiť `m`, keď sa spustí skutočný rekordér, a časy kapitol sadnú
na publikované audio, nie na hodiny aplikácie.

---

## 4. Technológie a prečo práve tieto

### Všetko, čo sa dotýka zvuku, beží lokálne

- Parakeet TDT 0.6B v3 cez MLX na Apple Silicon: 0,25 s na výpoveď, real-time faktor 0,05
- Výpovede krája Silero VAD, cez onnxruntime namiesto PyTorchu
- Zvuk nikdy neopustí počítač. Von ide iba text osnovy a text prepisu
- Súbor s osnovou sa otvára na čítanie a nikdy na zápis. Aplikácia anotuje kópiu

### Vzdialené je len uvažovanie

- DeepSeek V4 Flash (-fast) na tick každých 25 sekúnd. Odpovedá asi za 5 s, obyčajný model za 38
- Claude Sonnet 5 na záverečné spracovanie. Nikto naň nečaká, takže môže byť pomalý a písať dobre
- Oboje cez Venice, takže obe pokryje jedna predplatená peňaženka
- Model sa píše ako `provider:model`, takže presun wrap-upu na Anthropic či OpenAI je jeden prepínač

### Časť, ktorú by som bránil najtvrdšie

Model navrhuje a rozhoduje obyčajný kód. Z ticku sa vráti zoznam kandidátov na zmenu aj s istotou
a deterministický reducer rozhodne, čo sa so stavom naozaj stane.

- ručná značka vždy vyhráva a na desať minút uzamkne model pred tou položkou
- nadpis je prebraný, až keď sú prebraté jeho vlastné body
- každý čas, ktorý si model vymyslí, sa zareže do okna prepisu, ktoré naozaj videl

Keby model vlastnil stav priamo, vedel by mi prepísať mapu uprostred rozhovoru a nemal by som ako
ho pri tom zastaviť.

### Nenápadné časti

- Žiadny build: čistý JavaScript, jeden vlastný Markdown renderer, WebSocket
- Celý stav je JSON súbor zapísaný sekundu po každej zmene, takže pád stojí jednu výpoveď
- Asi 13 000 riadkov Pythonu, asi 400 testov, na macOS žiadny PyTorch

## 5. Koľko to stojí

- Jeden tick stojí asi 0,0009 $, lebo väčšina promptu sa vracia z cache
- Wrap-up stojí 0,14 až 0,20 $ podľa dĺžky poznámok, ktoré napíše, nie prepisu, ktorý prečíta
- Namerané na skutočnej šesťminútovej relácii: 0,17 $, 11 tickov, nič nezlyhalo
- Dvojhodinová epizóda vyjde asi na 0,50 $. Cieľ bol pod jeden dolár

## 6. Čo nie je vyriešené

- Pri jednom spoločnom mikrofóne nie sú žiadne značky hovoriacich a aplikácia to ani nepredstiera
- Slovenský prepis je presne taký dobrý ako mikrofón
	- cez bluetooth slúchadlá model ušiel do poľštiny a raz do ruštiny
	- vynútenie jazyka opraví ten útek, nie samotné slová
- Parakeetu sa nedá povedať, aký jazyk počuje. Whisperu áno, za desaťnásobnú latenciu
- Prahy pre „prebrané“ sú stále odhad, ktorý potrebuje pár skutočných epizód

**Otázka na záver:** čo by som chcel, aby to vedelo a zatiaľ nevie?

---

## Poznámky pre seba počas nahrávania

- Nekomentovať kód. Komentovať mapu
- Ak sa rozsvieti téma, ktorú som neplánoval, povedať to nahlas. To je tá funkcia v akcii
- Nechať niekde dlhšie ticho, aby tick vystrelil pred kamerou
