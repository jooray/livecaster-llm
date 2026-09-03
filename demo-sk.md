# Livecaster — živý kopilot pre podcastera

**Čo to je:** screencast o nástroji, ktorý prepisuje práve túto nahrávku.
**Formát:** sám, jeden mikrofón, ~15 minút.
**Pravidlo pre seba:** najprv to ukázať bežať, až potom vysvetľovať, ako to funguje.

---

## 1. Problém

- Nahrávam dlhé rozhovory a ku každému si píšem osnovu
- V rozhovore stratím prehľad, čo sme už prebrali — a zistím to až pri strihu
- Dve typické zlyhania: pýtam sa na to, čo už odznelo, a zabudnem na jedinú otázku, na ktorej mi záležalo
- Papierové poznámky sa samy neaktualizujú a druhá obrazovka plná textu je horšia než nič

**Otázka, ktorú treba na kameru zodpovedať:** prečo to nie je len „pusti Whisper po nahrávaní"?

## 2. Čo to robí — ukázať, nie opisovať

- Otvoriť mapu v prehliadači: osnova je celé rozhranie, nič iné tam nie je
- Rozprávať o téme, počkať, sledovať ako sa riadok preškrtne aj s časom
- Ukázať tri stavy: ~~prebrané~~, ◐ dotknuté, ⏭ preskočené
	- „prebrané" musí mať doslovný citát z prepisu ako dôkaz, nie pocit
	- letmá zmienka je len „dotknuté"
- Ukázať **Now** a **Next**: jeden riadok a tri návestia po piatich slovách
	- klávesa `d` odkryje zdôvodnenie modelu a jeho návrh premostenia
	- pointa je, že počas rozprávania nikdy nečítaš odstavce
- Stlačiť `c` na tému, ktorú model prehliadol — a ukázať, že potom má na desať minút zákaz vstupu
- Ukázať **Mentions**: knihy, ľudia a odkazy vytiahnuté z rozhovoru priebežne, ako zaznejú
- Stlačiť **Finish** a otvoriť záložku Result

## 3. Čo z toho vypadne na konci

- Show notes: zhrnutie, kapitoly s časmi, návrhy titulkov, popisy, jeden príspevok na sociálne siete
- Pôvodná osnova, znak po znaku, doplnená o to, čo sa prebralo a kedy
- Prepis ako Markdown a ako SRT titulky
- Surové JSON, aby sa poznámky dali pregenerovať bez ďalšieho platenia modelu

**Ukázať:** synchronizačnú značku. Stlačiť `m`, keď sa spustí skutočný rekordér, a všetky časy kapitol
sadnú na publikované audio, nie na hodiny aplikácie.

---

## 4. Technológie a prečo práve tieto

### Všetko, čo sa dotýka zvuku, beží lokálne

- **Parakeet TDT 0.6B v3** cez MLX na Apple Silicon — 0,25 s na výpoveď, real-time faktor 0,05
- **Silero VAD** na krájanie výpovedí, cez onnxruntime namiesto PyTorchu
- Zvuk nikdy neopustí počítač. Von ide iba text osnovy a text prepisu
- Súbor s osnovou sa vždy otvára len na čítanie. Aplikácia anotuje kópiu

### Vzdialené je len uvažovanie

- **DeepSeek V4 Flash (-fast)** na tick každých 25 sekúnd — odpovedá za ~5 s namiesto ~38 s
- **Claude Sonnet 5** na záverečné spracovanie, kde nezáleží na latencii, ale na písaní
- Oboje cez **Venice**, takže obe pokrýva jedna predplatená peňaženka
- Model sa píše ako `provider:model`, takže presun wrap-upu na Anthropic či OpenAI je jeden prepínač

### Časť, ktorú by som bránil najtvrdšie

- **LLM navrhuje, deterministický reducer rozhoduje.** Model vráti kandidátov na zmenu aj s istotou;
  o tom, čo sa so stavom naozaj stane, rozhoduje obyčajný kód
	- ručná značka vždy vyhráva a na desať minút uzamkne model
	- nadpis je prebraný, až keď sú prebrané jeho položky
	- každý čas, ktorý si model vymyslí, sa zareže do okna prepisu, ktoré naozaj videl
- Prečo: jazykový model, ktorý vlastní tvoj stav, ti ho raz prepíše naživo pred hosťom

### Voľby, ktoré vyzerajú nudne a nie sú

- Žiadny build. Čisté JavaScript, jeden vlastný Markdown renderer, WebSocket
- Celý stav je JSON súbor zapísaný sekundu po každej zmene — pád stojí jednu výpoveď
- ~13 000 riadkov Pythonu, ~400 testov, na macOS žiadny PyTorch

## 5. Koľko to stojí

- Tick: približne **$0,0009** — väčšina promptu sa vracia z cache
- Wrap-up: **$0,14–0,20**, podľa dĺžky poznámok, ktoré napíše, nie prepisu, ktorý prečíta
- Namerané na skutočnej šesťminútovej relácii: **$0,17**, 11 tickov, žiadne zlyhanie
- Dvojhodinová epizóda vyjde asi na **$0,50**. To bol cieľ: hlboko pod jeden dolár

## 6. Čo úprimne nie je vyriešené

- Rozlíšenie hovoriacich pri jednom spoločnom mikrofóne: nie je žiadne a aplikácia to nepredstiera
- Slovenský prepis je presne taký dobrý ako mikrofón
	- cez bluetooth slúchadlá model ušiel do poľštiny a ruštiny
	- vynútenie jazyka opraví ten útek, nie samotné slová
- Parakeetu sa nedá povedať, aký jazyk počuje; Whisperu áno, za desaťnásobnú latenciu
- Prahy pre „prebrané" sú stále odhad, ktorý potrebuje pár skutočných epizód

**Otázka na záver:** čo by som chcel, aby to vedelo a zatiaľ nevie?

---

## Poznámky pre seba počas nahrávania

- Nekomentovať kód. Komentovať mapu.
- Ak sa rozsvieti téma, ktorú som neplánoval, povedať to nahlas — to je tá funkcia v akcii.
- Nechať niekde dlhšie ticho, aby bolo vidieť, ako tick vystrelí sám od seba.
