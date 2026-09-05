"""Cœur de simulation CMS-Lab.

Aucun module de ce paquet ne fait d'entrée-sortie : ni réseau, ni fichier, ni
affichage. C'est cette contrainte qui permet de faire tourner le simulateur à
vingt fois la vitesse réelle pour produire des statistiques, puis de rejouer
un run dans la console, avec exactement le même code.
"""
