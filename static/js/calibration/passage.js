/* The calibration reading passage.

   Original text (no copyrighted material), written so that the ten sentences
   together contain every General American English phoneme at least once,
   plus the common clusters (/st/ /str/ /pl/ /fr/ /sm/ /tw/ /kw/ /mp/ /ld/
   /nd/ /ŋk/). Sentences are short — seven words or fewer — because short
   cards are kinder to fatigue and give clean per-sentence audio.

   `focus` is a human-readable note of the less common sounds a sentence
   exercises; the finish screen uses it, quietly. */

export const PASSAGE = [
  { id: 's01', text: 'The sun rose over the quiet harbor.',  focus: 'th · s · kw' },
  { id: 's02', text: 'A pot of fresh coffee smells good.',   focus: 'p · fr · sm' },
  { id: 's03', text: 'She watched the children jump and cheer.', focus: 'sh · ch · j' },
  { id: 's04', text: 'Both of my thumbs ache in the cold.',  focus: 'th · long o · ld' },
  { id: 's05', text: 'Measure twice before you choose the beige rug.', focus: 'zh · tw · ch' },
  { id: 's06', text: 'Would you put the good book down?',    focus: 'oo · w · y' },
  { id: 's07', text: 'Five noisy boys enjoyed the loud song.', focus: 'oy · ow · ng' },
  { id: 's08', text: 'Her first words were clear and strong.', focus: 'er · str · ng' },
  { id: 's09', text: 'Please ask them to open the window.',  focus: 'pl · a · nd' },
  { id: 's10', text: 'Thank you for hearing my voice today.', focus: 'nk · h · oy' },
];

export function words(sentence) {
  return sentence.text.split(/\s+/);
}
