(*
   IPMS — conduite de la production d'eau glacée des baies radar.

   C'est le programme qui rend le lab intéressant : il ne pilote pas le CMS,
   il pilote la plateforme. Et l'état de la plateforme conditionne ce que le
   CMS peut voir — température haute, émission déclassée, horizon de
   détection réduit.

   Adressage. Les points distants (capteurs interrogés par OpenPLC en tant
   que maître) sont mappés à partir de l'adresse 100. Les %Q sont l'image
   transmise au CMS par le serveur Modbus de l'automate. Vérifier le mapping
   réellement attribué dans l'interface web, page Slave Devices : c'est elle
   qui fait foi, pas ce commentaire.

   Voir modbus-map.md.
*)

PROGRAM ipms
  (* matiec exige que les variables localisées (AT) et les variables
     internes soient déclarées dans des blocs VAR distincts : dès qu'une
     ligne sans AT suit une ligne avec AT dans le même bloc, son analyseur
     continue d'exiger AT et rejette la suite avec « invalid located
     variable declaration », puis en cascade toutes les instructions qui
     utilisent ces variables plus loin dans le programme. Vérifié en
     compilant avec le iec2c réellement utilisé par OpenPLC_v3. *)
  VAR
    (* --- entrées terrain, lues par l'automate maître --- *)
    temp_brute   AT %IW100  : INT;     (* dixièmes de °C   *)
    press_brute  AT %IW101  : INT;     (* centibars        *)
    rpm_brute    AT %IW102  : INT;     (* dixièmes de tr/min *)
    rot_antenne  AT %IX100.0 : BOOL;
    pompe_marche AT %IX100.1 : BOOL;

    (* --- image transmise au CMS --- *)
    out_temp     AT %QW0    : INT;
    out_press    AT %QW1    : INT;
    out_rpm      AT %QW2    : INT;
    out_puiss    AT %QW3    : INT;     (* dixièmes de %    *)
    cms_rot      AT %QX0.0  : BOOL;
    cms_pompe    AT %QX0.1  : BOOL;
    cms_al_temp  AT %QX0.2  : BOOL;
    cms_al_trip  AT %QX0.3  : BOOL;
  END_VAR

  VAR
    (* --- interne --- *)
    seuil_haut   : BOOL;
    tempo_alarme : TON;
    puiss_calc   : REAL;
  END_VAR

  (* Hystérésis : on arme l'alarme à 70 °C, on ne la désarme qu'à 66 °C.
     Sans cela, une valeur qui oscille autour du seuil fait clignoter
     l'alarme et l'opérateur cesse de la regarder. *)
  IF temp_brute >= 700 THEN
    seuil_haut := TRUE;
  ELSIF temp_brute <= 660 THEN
    seuil_haut := FALSE;
  END_IF;

  (* Temporisation anti-rebond : un pic de trois secondes sur un capteur
     n'est pas une avarie de refroidissement. *)
  tempo_alarme(IN := seuil_haut, PT := T#5s);
  cms_al_temp := tempo_alarme.Q;

  (* Coupure d'émission. Au-delà de 88 °C on ne déclasse plus, on arrête :
     protéger l'émetteur prime sur la veille, parce qu'un émetteur détruit
     ne revient pas en service à la fin de l'alerte. *)
  cms_al_trip := temp_brute >= 880;

  (* Déclassement progressif entre 70 et 88 °C, en dixièmes de pour cent. *)
  IF cms_al_trip THEN
    puiss_calc := 20.0;
  ELSIF temp_brute <= 700 THEN
    puiss_calc := 1000.0;
  ELSE
    puiss_calc := 1000.0 - 850.0 * (INT_TO_REAL(temp_brute) - 700.0) / 180.0;
  END_IF;

  out_temp  := temp_brute;
  out_press := press_brute;
  out_rpm   := rpm_brute;
  out_puiss := REAL_TO_INT(puiss_calc);
  cms_rot   := rot_antenne;
  cms_pompe := pompe_marche;
END_PROGRAM

(*
   ARMEMENT — séquencement du lanceur : munitions, canal occupé, interlock.

   Volontairement distinct de l'IPMS ci-dessus, comme la conduite de
   plateforme et la conduite du combat sont deux systèmes séparés à bord.
   Ce programme ne décide JAMAIS quoi engager ni sur quelle piste — ça reste
   le rôle de sim/tewa.py côté CMS. Il ne fait que ce qu'un vrai automate de
   lanceur fait réellement : compter les coups partis, refuser un ordre tant
   qu'un canal est occupé, remonter un défaut d'interlock.

   Commande CMS -> automate : un mot mémoire, pas une bobine %Q. Une bobine
   %Q est réécrite à chaque cycle par CE programme (voir cms_pompe plus
   haut, même piège que le F10 console avant son correctif côté serveur) ;
   %MW0 n'est touché que par le CMS en écriture et par ce programme en
   lecture, donc rien ne l'écrase entre deux ordres.

   Front montant sur %MW0 = code effecteur : un ordre qui reste affiché ne
   redéclenche pas un second coup tant que le CMS ne l'a pas changé (remis
   à 255, puis reposé) — c'est R_TRIG qui porte cette règle, pas une
   temporisation arbitraire.

   Voir modbus-map.md.
*)
PROGRAM armement
  VAR
    (* --- commande, écrite par le CMS --- *)
    cmd_effecteur AT %MW0   : INT;    (* 0=SAM 1=CIWS 2=GUN 3=SSM 255=aucun *)

    (* --- image transmise au CMS --- *)
    out_muni_sam  AT %QW10  : INT;
    out_muni_ciws AT %QW11  : INT;
    out_muni_gun  AT %QW12  : INT;
    out_muni_ssm  AT %QW13  : INT;

    pret_sam      AT %QX1.0 : BOOL;   (* canal libre, munitions restantes, pas de défaut *)
    pret_ciws     AT %QX1.1 : BOOL;
    pret_gun      AT %QX1.2 : BOOL;
    pret_ssm      AT %QX1.3 : BOOL;

    defaut_sam    AT %QX2.0 : BOOL;   (* point d'extension : câbler un vrai interlock ici *)
    defaut_ciws   AT %QX2.1 : BOOL;
    defaut_gun    AT %QX2.2 : BOOL;
    defaut_ssm    AT %QX2.3 : BOOL;
  END_VAR

  VAR
    (* Dotations initiales et maximales — mêmes valeurs que sim/tewa.py
       Effector.rounds, pour rester cohérent avec le modèle logiciel qu'on
       remplace ici. Le rechargement automatique ne dépasse jamais cette
       borne : un magasin ne grossit pas tout seul. *)
    muni_sam   : INT := 16;
    muni_ciws  : INT := 999;
    muni_gun   : INT := 120;
    muni_ssm   : INT := 8;

    (* Temps de recyclage du canal après un coup — à ajuster librement,
       aucune valeur du domaine ne les impose. *)
    tempo_sam  : TON;
    tempo_ciws : TON;
    tempo_gun  : TON;
    tempo_ssm  : TON;

    front_sam  : R_TRIG;
    front_ciws : R_TRIG;
    front_gun  : R_TRIG;
    front_ssm  : R_TRIG;

    (* Rechargement automatique : un coup revient tout seul après un délai
       qui dépend du type d'arme — un canon à chargeur (CIWS) recharge vite,
       un missile antinavire (SSM) est le plus lourd à remettre en œuvre.
       Un seul TON par effecteur, qui se réarme lui-même via le front de son
       propre passage à vrai : le motif classique d'oscillateur à un seul
       bloc fonction, pas deux appels du même bloc dans un même cycle. *)
    tempo_rech_sam  : TON;
    tempo_rech_ciws : TON;
    tempo_rech_gun  : TON;
    tempo_rech_ssm  : TON;
    front_rech_sam  : R_TRIG;
    front_rech_ciws : R_TRIG;
    front_rech_gun  : R_TRIG;
    front_rech_ssm  : R_TRIG;
  END_VAR

  front_sam(CLK := cmd_effecteur = 0);
  IF front_sam.Q AND muni_sam > 0 AND NOT tempo_sam.Q THEN
    muni_sam := muni_sam - 1;
  END_IF;
  tempo_sam(IN := front_sam.Q, PT := T#3S);

  front_rech_sam(CLK := tempo_rech_sam.Q);
  IF front_rech_sam.Q AND muni_sam < 16 THEN
    muni_sam := muni_sam + 1;
  END_IF;
  tempo_rech_sam(IN := muni_sam < 16 AND NOT tempo_rech_sam.Q, PT := T#15S);

  pret_sam     := muni_sam > 0 AND NOT tempo_sam.Q;
  defaut_sam   := FALSE;
  out_muni_sam := muni_sam;

  front_ciws(CLK := cmd_effecteur = 1);
  IF front_ciws.Q AND muni_ciws > 0 AND NOT tempo_ciws.Q THEN
    muni_ciws := muni_ciws - 1;
  END_IF;
  tempo_ciws(IN := front_ciws.Q, PT := T#1S);

  front_rech_ciws(CLK := tempo_rech_ciws.Q);
  IF front_rech_ciws.Q AND muni_ciws < 999 THEN
    muni_ciws := muni_ciws + 1;
  END_IF;
  tempo_rech_ciws(IN := muni_ciws < 999 AND NOT tempo_rech_ciws.Q, PT := T#5S);

  pret_ciws     := muni_ciws > 0 AND NOT tempo_ciws.Q;
  defaut_ciws   := FALSE;
  out_muni_ciws := muni_ciws;

  front_gun(CLK := cmd_effecteur = 2);
  IF front_gun.Q AND muni_gun > 0 AND NOT tempo_gun.Q THEN
    muni_gun := muni_gun - 1;
  END_IF;
  tempo_gun(IN := front_gun.Q, PT := T#2S);

  front_rech_gun(CLK := tempo_rech_gun.Q);
  IF front_rech_gun.Q AND muni_gun < 120 THEN
    muni_gun := muni_gun + 1;
  END_IF;
  tempo_rech_gun(IN := muni_gun < 120 AND NOT tempo_rech_gun.Q, PT := T#10S);

  pret_gun     := muni_gun > 0 AND NOT tempo_gun.Q;
  defaut_gun   := FALSE;
  out_muni_gun := muni_gun;

  front_ssm(CLK := cmd_effecteur = 3);
  IF front_ssm.Q AND muni_ssm > 0 AND NOT tempo_ssm.Q THEN
    muni_ssm := muni_ssm - 1;
  END_IF;
  tempo_ssm(IN := front_ssm.Q, PT := T#5S);

  front_rech_ssm(CLK := tempo_rech_ssm.Q);
  IF front_rech_ssm.Q AND muni_ssm < 8 THEN
    muni_ssm := muni_ssm + 1;
  END_IF;
  tempo_rech_ssm(IN := muni_ssm < 8 AND NOT tempo_rech_ssm.Q, PT := T#20S);

  pret_ssm     := muni_ssm > 0 AND NOT tempo_ssm.Q;
  defaut_ssm   := FALSE;
  out_muni_ssm := muni_ssm;
END_PROGRAM

(*
   MACHINE — barre et propulsion.

   Troisième sous-programme, même principe que les deux précédents :
   l'automate anime ses propres organes (aiguille de RPM, mèche de barre)
   à sa propre cadence, il ne décide jamais de la route du porteur — ça
   reste sim/entities.py Ownship côté CMS. Cette première version ne fait
   donc que rejouer en façade l'ordre déjà écrit par le CMS (télégraphe
   et angle de barre), avec une rampe réaliste au lieu d'un saut
   instantané — le CMS garde la vérité physique tant qu'aucune boucle
   Modbus ne referme l'autorité dessus (voir modbus-map.md).

   Commande CMS -> automate : quatre mots mémoire, pas des bobines %Q —
   même raison que %MW0 pour l'armement, cms_pompe plus haut en a payé le
   prix avant son correctif : une bobine %Q est réécrite par CE programme
   à chaque cycle, un ordre du CMS dessus serait aussitôt perdu.
   Contrairement à %MW0, ce ne sont pas des fronts : la vitesse et le cap
   restent ordonnés en continu, pas déclenchés un coup à la fois — pas de
   R_TRIG ici, une simple lecture de niveau à chaque cycle suffit.

   %MW1/%MW2 (télégraphe, angle de barre) sont ce que ce programme utilise
   réellement pour sa rampe. %MW3/%MW4 (cap et vitesse ordonnés bruts) ne
   pilotent rien ici — cmd_telegraphe/cmd_barre restent les seules
   entrées de la logique — mais l'automate doit les recevoir lui aussi :
   sans eux, sa page Monitoring ne montre qu'un cran (0-4) et un angle
   dérivés, jamais les valeurs qu'un opérateur a réellement ordonnées
   (cap en degrés, vitesse en nœuds). Aucune conversion à leur faire
   subir ici, le CMS l'a déjà faite en écrivant %MW1/%MW2.

   Tableau électrique (%MW5-7, %QW22, %QX3.0-3.3) : ici l'automate a une
   vraie autorité, contrairement au reste du programme — il refuse de
   fermer le disjoncteur tant que le générateur n'est pas stable, quel
   que soit ce que le CMS demande. Pas de bobine %MX pour les commandes
   booléennes : son adressage Modbus n'est pas établi dans ce projet
   (contrairement à %MW, vérifié dans webserver/core/modbus.cpp), donc
   même schéma que le reste — un mot mémoire par commande, 0/1 en guise
   de booléen, pour rester sur un mécanisme déjà prouvé plutôt que d'en
   deviner un nouveau.
*)
PROGRAM machine
  VAR
    (* --- commande, écrite par le CMS --- *)
    cmd_telegraphe  AT %MW1  : INT;   (* 0=stop 1=lente 2=demi 3=pleine 4=toute *)
    cmd_barre       AT %MW2  : INT;   (* degrés, -35..35, angle de barre ordonné *)
    cmd_cap_brut    AT %MW3  : INT;   (* degrés, 0..359, cap ordonné tel quel *)
    cmd_vit_brute   AT %MW4  : INT;   (* dixièmes de nœud, vitesse ordonnée telle quelle *)
    cmd_batterie    AT %MW5  : INT;   (* 0/1 *)
    cmd_generateur  AT %MW6  : INT;   (* 0/1 *)
    cmd_disjoncteur AT %MW7  : INT;   (* 0/1, demandé — pas forcément accordé *)

    (* --- image transmise au CMS --- *)
    out_rpm         AT %QW20 : INT;   (* tr/min *)
    out_barre_reel  AT %QW21 : INT;   (* degrés, signé *)
    out_gen_progres AT %QW22 : INT;   (* 0-100, progression du démarrage générateur *)
    batterie_en_ligne AT %QX3.0 : BOOL;
    generateur_pret   AT %QX3.1 : BOOL;
    disjoncteur_ferme AT %QX3.2 : BOOL;
    propulsion_dispo  AT %QX3.3 : BOOL;
  END_VAR

  VAR
    (* Cinq crans façon télégraphe de route, pas un curseur continu — un
       automate de salle des machines répond à un ordre discret, pas à
       une valeur analogique arbitraire. Chiffres ronds pour ce labo, pas
       la fiche technique d'un moteur réel. *)
    rpm_cible   : REAL;
    rpm_reel    : REAL;
    PAS_RPM     : REAL := 0.4;    (* rpm par cycle de 50 ms : 0 à 240 en ~30 s *)

    barre_cible : REAL;
    barre_reel  : REAL;
    PAS_BARRE   : REAL := 0.25;   (* degrés par cycle : -35 à 35 en ~14 s *)

    (* Progression de démarrage en pourcentage plutôt que TON.ET converti
       en temps — même choix que rpm_reel/barre_reel : un accumulateur
       borné par cycle, pas de conversion TIME<->REAL dont le support
       varie d'un compilateur IEC à l'autre. *)
    gen_demarrage : REAL := 0.0;
    PAS_GEN       : REAL := 0.417;  (* % par cycle de 50 ms : 0 à 100 en 12 s *)
  END_VAR

  (* --- tableau électrique --- *)
  (* La batterie répond tout de suite, un générateur non : c'est le
     délai de stabilisation qui rend la séquence réelle plutôt que trois
     bobines qui s'allument en même temps. Couper la batterie ou l'ordre
     générateur en cours de démarrage remet à zéro plutôt que de figer la
     progression — un vrai groupe qui perd son alimentation ne reprend
     pas où il en était. *)
  batterie_en_ligne := cmd_batterie = 1;

  IF batterie_en_ligne AND cmd_generateur = 1 THEN
    IF gen_demarrage < 100.0 THEN
      gen_demarrage := gen_demarrage + PAS_GEN;
      IF gen_demarrage > 100.0 THEN gen_demarrage := 100.0; END_IF;
    END_IF;
  ELSE
    gen_demarrage := 0.0;
  END_IF;
  generateur_pret := gen_demarrage >= 100.0;
  out_gen_progres := REAL_TO_INT(gen_demarrage);

  (* Le seul vrai interlock de ce programme : le disjoncteur refuse de se
     fermer sans générateur stable, quoi que le CMS demande sur
     cmd_disjoncteur. La propulsion en dépend directement — pas de
     surprise, c'est le même bit. *)
  disjoncteur_ferme := cmd_disjoncteur = 1 AND generateur_pret;
  propulsion_dispo  := disjoncteur_ferme;

  IF NOT propulsion_dispo THEN
    rpm_cible := 0.0;
  ELSIF cmd_telegraphe <= 0 THEN
    rpm_cible := 0.0;
  ELSIF cmd_telegraphe = 1 THEN
    rpm_cible := 60.0;
  ELSIF cmd_telegraphe = 2 THEN
    rpm_cible := 120.0;
  ELSIF cmd_telegraphe = 3 THEN
    rpm_cible := 180.0;
  ELSE
    rpm_cible := 240.0;
  END_IF;

  IF rpm_reel < rpm_cible THEN
    rpm_reel := rpm_reel + PAS_RPM;
    IF rpm_reel > rpm_cible THEN rpm_reel := rpm_cible; END_IF;
  ELSIF rpm_reel > rpm_cible THEN
    rpm_reel := rpm_reel - PAS_RPM;
    IF rpm_reel < rpm_cible THEN rpm_reel := rpm_cible; END_IF;
  END_IF;

  (* Défense contre un ordre hors gabarit plutôt qu'une mèche qui parte
     au-delà de sa course mécanique réelle. *)
  IF cmd_barre > 35 THEN
    barre_cible := 35.0;
  ELSIF cmd_barre < -35 THEN
    barre_cible := -35.0;
  ELSE
    barre_cible := INT_TO_REAL(cmd_barre);
  END_IF;

  IF barre_reel < barre_cible THEN
    barre_reel := barre_reel + PAS_BARRE;
    IF barre_reel > barre_cible THEN barre_reel := barre_cible; END_IF;
  ELSIF barre_reel > barre_cible THEN
    barre_reel := barre_reel - PAS_BARRE;
    IF barre_reel < barre_cible THEN barre_reel := barre_cible; END_IF;
  END_IF;

  out_rpm        := REAL_TO_INT(rpm_reel);
  out_barre_reel := REAL_TO_INT(barre_reel);
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK cyclique(INTERVAL := T#50ms, PRIORITY := 0);
    TASK cyclique_armement(INTERVAL := T#50ms, PRIORITY := 1);
    TASK cyclique_machine(INTERVAL := T#50ms, PRIORITY := 2);
    PROGRAM inst0 WITH cyclique : ipms;
    PROGRAM inst1 WITH cyclique_armement : armement;
    PROGRAM inst2 WITH cyclique_machine : machine;
  END_RESOURCE
END_CONFIGURATION
