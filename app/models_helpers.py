# Generated with:
#     isoquery --iso 639-3 | cut -d$'\t' -f1,4 | grep -P '\t..' | sed -r 's/\t(..)/="\1",/'
_ISO639_3TO1 = dict(
    aar="aa", abk="ab", afr="af", aka="ak", amh="am", ara="ar", arg="an", asm="as", ava="av",
    ave="ae", aym="ay", aze="az", bak="ba", bam="bm", bel="be", ben="bn", bis="bi", bod="bo",
    bos="bs", bre="br", bul="bg", cat="ca", ces="cs", cha="ch", che="ce", chu="cu", chv="cv",
    cor="kw", cos="co", cre="cr", cym="cy", dan="da", deu="de", div="dv", dzo="dz", ell="el",
    eng="en", epo="eo", est="et", eus="eu", ewe="ee", fao="fo", fas="fa", fij="fj", fin="fi",
    fra="fr", fry="fy", ful="ff", gla="gd", gle="ga", glg="gl", glv="gv", grn="gn", guj="gu",
    hat="ht", hau="ha", hbs="sh", heb="he", her="hz", hin="hi", hmo="ho", hrv="hr", hun="hu",
    hye="hy", ibo="ig", ido="io", iii="ii", iku="iu", ile="ie", ina="ia", ind="id", ipk="ik",
    isl="is", ita="it", jav="jv", jpn="ja", kal="kl", kan="kn", kas="ks", kat="ka", kau="kr",
    kaz="kk", khm="km", kik="ki", kin="rw", kir="ky", kom="kv", kon="kg", kor="ko", kua="kj",
    kur="ku", lao="lo", lat="la", lav="lv", lim="li", lin="ln", lit="lt", ltz="lb", lub="lu",
    lug="lg", mah="mh", mal="ml", mar="mr", mkd="mk", mlg="mg", mlt="mt", mon="mn", mri="mi",
    msa="ms", mya="my", nau="na", nav="nv", nbl="nr", nde="nd", ndo="ng", nep="ne", nld="nl",
    nno="nn", nob="nb", nor="no", nya="ny", oci="oc", oji="oj", ori="or", orm="om", oss="os",
    pan="pa", pli="pi", pol="pl", por="pt", pus="ps", que="qu", roh="rm", ron="ro", run="rn",
    rus="ru", sag="sg", san="sa", sin="si", slk="sk", slv="sl", sme="se", smo="sm", sna="sn",
    snd="sd", som="so", sot="st", spa="es", sqi="sq", srd="sc", srp="sr", ssw="ss", sun="su",
    swa="sw", swe="sv", tah="ty", tam="ta", tat="tt", tel="te", tgk="tg", tgl="tl", tha="th",
    tir="ti", ton="to", tsn="tn", tso="ts", tuk="tk", tur="tr", twi="tw", uig="ug", ukr="uk",
    urd="ur", uzb="uz", ven="ve", vie="vi", vol="vo", wln="wa", wol="wo", xho="xh", yid="yi",
    yor="yo", zha="za", zho="zh", zul="zu",
)


def to_iso639(lang):
    if isinstance(lang, str) and '-' in lang:
        # Handle IETF/BCP47 language tags, such as "en-US",
        # "ar-aeb" (Arabic as spoken in Tunis)
        lang = lang[:lang.index('-')]
    return _ISO639_3TO1.get(lang, lang)


# UniversalDependencies to Lexinfo POS and vice versa
# https://universaldependencies.org/u/pos/
_UD2LEXINFO = {
    'ADJ': 'adjective',
    'ADP': 'adposition',
    'ADV': 'adverb',
    'AUX': 'auxiliary',
    'CCONJ': 'coordinatingConjunction',
    'DET': 'determiner',
    'INTJ': 'interjection',
    'NOUN': 'commonNoun',
    'NUM': 'numeral',
    'PART': 'particle',
    'PRON': 'pronoun',
    'PROPN': 'properNoun',
    'PUNCT': 'punctuation',
    'SCONJ': 'subordinatingConjunction',
    'SYM': 'symbol',
    'VERB': 'verb',
    'X': 'other',
}

_LEXINFO2UD = {v: k for k, v in _UD2LEXINFO.items()}


def lexinfo_pos_to_ud(pos):
    return _LEXINFO2UD.get(pos, pos)


def ud_to_lexinfo_pos(ud_pos):
    return _UD2LEXINFO.get(ud_pos, ud_pos)
