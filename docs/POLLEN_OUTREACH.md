# Getting measured pollen data for Sofia

DishAI's pollen numbers are **modelled** (CAMS European forecast via Open-Meteo, ~11 km). We found no open measured pollen counts for Sofia.
Measured counts would let us check the model, so these are the two people to ask. Nothing below has been sent; it is a draft.

## Who to ask

1. **European Aeroallergen Network (EAN)**, Medical University of Vienna / polleninformation.at. Holds trap data from 600+ sites; it says the network
   has been extended to Bulgaria, but we found no station list, and access is limited to data contributors and approved users. Ask: which Bulgarian
   stations report to EAN, whether Sofia has one, and what terms apply to showing daily values in a public, non-commercial app.
   Info: https://www.polleninformation.at/info/en/what-is-ean
2. **National Centre for Infectious and Parasitic Diseases (Sofia)**. Publishes a seasonal ragweed outlook as text (reported by BTA in August 2024).
   Ask: do they run a pollen trap in Sofia, can they share daily counts or a structured version of the outlook, and under what credit and licence.

## Draft message (adapt before sending)

> Subject: Pollen counts for Sofia: can we show them (and check our model against them)?
>
> Hello,
>
> We build DishAI, a free public app for Sofia that shows air quality, heat and a pollen forecast per plant, with a plan-linked advice for residents.
> Today our pollen values are modelled (CAMS, via Open-Meteo), which is only an approximation of what a trap catches. We would like to:
> 1. show your measured daily counts for Sofia next to the model, with full credit to you, and
> 2. use them only to check how well the model matches, and to tell users honestly when it does not.
>
> Could you tell us whether a Sofia sampler reports to you, what the access terms are, and what credit you would like?
> We store no personal data. The app is non-commercial.
>
> Thank you,
> (name, contact)

## What we would do with it

* Show "measured" next to "modelled" on the Allergy tab and flag disagreement.
* Compare with the anonymous symptom check-ins (`/api/symptoms/summary`): do reports rise on the days the counts are high?
* Replace the hand-set bands in `backend/advice.py` (`POLLEN_BANDS`) with ones fitted to local counts and symptoms.
